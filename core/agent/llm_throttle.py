"""Throttled, redacting Gemini client for the audit agent.

Why this module exists:

* The free-tier Gemini API allows ~15 requests/minute. The agent's ReAct loop
  makes several LLM calls per invoice, so naive calling gets 429s and kills
  eval runs. ``RateLimiter`` paces calls with a safety margin + jitter.
* A previous incident leaked a Gemini API key into ``evals/baseline_report.md``
  because exception text contained the request URL with ``?key=...``. Every
  error raised here is passed through ``redact()`` first.
* Only free-tier models may be used. ``assert_free_tier`` enforces an
  allowlist (overridable via ``GEMINI_FREE_TIER_MODELS``); paid models fail
  fast instead of silently burning quota.
"""
from __future__ import annotations

import os
import random
import re
import threading
import time
from typing import Dict, Optional

import requests

# Free-tier Gemini models this project is allowed to call. The allowlist is
# deliberately narrow: "free tier" must be provable, not assumed. Override with
# GEMINI_FREE_TIER_MODELS="model-a,model-b" if Google changes the lineup.
FREE_TIER_MODELS = frozenset(
    m.strip()
    for m in os.getenv(
        "GEMINI_FREE_TIER_MODELS",
        "gemini-3.1-flash-lite,gemini-2.5-flash-lite,gemini-2.5-flash,"
        "gemini-2.0-flash,gemini-2.0-flash-lite,gemini-flash-latest",
    ).split(",")
    if m.strip()
)

# Conservative default: 15 RPM free tier -> one call per 4s, plus margin.
DEFAULT_RPM = float(os.getenv("GEMINI_FREE_TIER_RPM", "15"))
SAFETY_FACTOR = 1.15

REDACTED = "<redacted>"


class PaidModelError(ValueError):
    """Raised when a non-free-tier model is requested."""


class CircuitOpenError(RuntimeError):
    """Raised when consecutive LLM failures trip the circuit breaker."""


def assert_free_tier(model: str) -> str:
    """Fail fast unless *model* is on the free-tier allowlist."""
    if model not in FREE_TIER_MODELS:
        raise PaidModelError(
            f"model {model!r} is not on the free-tier allowlist "
            f"({sorted(FREE_TIER_MODELS)}). Refusing to call a possibly paid "
            f"model. Override with GEMINI_FREE_TIER_MODELS if this is wrong."
        )
    return model


def redact(text: object, api_key: Optional[str] = None) -> str:
    """Replace API-key occurrences (and ?key=... URL params) in *text*."""
    s = str(text)
    key = api_key or os.getenv("GEMINI_API_KEY", "")
    if key and len(key) > 6:
        s = s.replace(key, REDACTED)
    # catch ?key=... / &key=... even when the key value isn't in env
    s = re.sub(r"([?&]key=)[^&\s'\"]+", r"\1" + REDACTED, s)
    return s


class RateLimiter:
    """Minimum-interval pacer with jitter. Thread-safe."""

    def __init__(self, rpm: float = DEFAULT_RPM, safety: float = SAFETY_FACTOR):
        self.min_interval = (60.0 / rpm) * safety
        self._lock = threading.Lock()
        self._next_ok = 0.0

    def wait(self) -> float:
        """Block until a call is allowed. Returns seconds waited."""
        with self._lock:
            now = time.monotonic()
            wait = self._next_ok - now
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.5))
            waited = max(0.0, time.monotonic() - now)
            self._next_ok = time.monotonic() + self.min_interval
            return waited


class GeminiClient:
    """Gemini generateContent client: paced, retried, redacting, free-tier only.

    Retries transient 429/5xx with exponential backoff; trips a circuit
    breaker after ``breaker_threshold`` consecutive failures so a dead API
    fails the run fast instead of hanging it.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        rpm: float = DEFAULT_RPM,
        max_retries: int = 3,
        breaker_threshold: int = 5,
        timeout: int = 60,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is required for live Gemini calls. "
                "Run with --mock-llm for a credential-free harness self-test."
            )
        self.model = assert_free_tier(
            model or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
        )
        self.base_url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        self.limiter = RateLimiter(rpm=rpm)
        self.max_retries = max_retries
        self.breaker_threshold = breaker_threshold
        self.timeout = timeout
        self._consecutive_failures = 0
        # Token usage of the most recent successful call, captured from the
        # response's usageMetadata. Consumed (reset to None) by the audit
        # agent after each call; absent/partial metadata stays None — never
        # invented.
        self.last_usage: Optional[Dict[str, Optional[int]]] = None

    def _redacted_url(self) -> str:
        return self.base_url + "?key=" + REDACTED

    def generate(
        self,
        prompt: str,
        temperature: float = 0.2,
        max_output_tokens: int = 1200,
    ) -> str:
        if self._consecutive_failures >= self.breaker_threshold:
            raise CircuitOpenError(
                f"circuit open: {self._consecutive_failures} consecutive Gemini "
                f"failures for model {self.model!r}; aborting run"
            )
        last: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                self.limiter.wait()
                resp = requests.post(
                    self.base_url,
                    params={"key": self.api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "temperature": temperature,
                            "maxOutputTokens": max_output_tokens,
                        },
                    },
                    timeout=self.timeout,
                )
                status = resp.status_code
                if status == 429 or 500 <= status < 600:
                    raise requests.HTTPError(
                        f"{status} from {self._redacted_url()}: "
                        f"{redact(resp.text[:300], self.api_key)}"
                    )
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usageMetadata") or {}
                self.last_usage = {
                    "prompt_tokens": usage.get("promptTokenCount"),
                    "candidates_tokens": usage.get("candidatesTokenCount"),
                    "total_tokens": usage.get("totalTokenCount"),
                }
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                self._consecutive_failures = 0
                return text
            except Exception as e:  # noqa: BLE001 - retried, then re-raised redacted
                last = e
                self._consecutive_failures += 1
                if attempt < self.max_retries:
                    time.sleep((2 ** attempt) * 2 + random.uniform(0, 1))
        raise RuntimeError(
            f"Gemini call failed after {self.max_retries + 1} attempts: "
            f"{redact(last, self.api_key)}"
        ) from last
