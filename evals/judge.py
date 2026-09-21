"""LLM-as-judge grader for the ProcureAI eval harness.

Scores one agent audit brief against a golden invoice record:

    verdict_match   bool   did the agent's risk_verdict equal the expected verdict?
    findings_recall float  fraction of expected findings the brief actually mentions (0-1)
    notes           str    human-readable explanation of the score

Two judge implementations:

* ``MockJudge`` (default) - pure rule-based, zero credentials. Compares verdict
  strings exactly (after normalization) and scores findings by keyword overlap
  between each expected finding and the brief's summary + cited rules + reasoning.
* ``GeminiJudge`` - calls the Gemini API directly with ``requests`` (no langchain,
  no extra deps) and asks the model to return a strict JSON score.

Select with the ``JUDGE_MODE`` env var: ``mock`` (default) or ``gemini``.
``GeminiJudge`` requires ``GEMINI_API_KEY``; the model defaults to ``GEMINI_MODEL``
or ``gemini-3.1-flash-lite`` (same default as core/agent_auditor.py).
"""

import json
import os
import re

import requests

VALID_VERDICTS = ("APPROVE", "FLAG", "REJECT")

VERDICT_ALIASES = {
    "APPROVE": "APPROVE",
    "APPROVED": "APPROVE",
    "ACCEPT": "APPROVE",
    "FLAG": "FLAG",
    "FLAGGED": "FLAG",
    "REJECT": "REJECT",
    "REJECTED": "REJECT",
    "DENY": "REJECT",
}

STOPWORDS = frozenset(
    "the a an and or of to in on for with is are was were be by as at from that this it its".split()
)

GEMINI_API_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def normalize_verdict(v):
    """Canonicalize a verdict string; returns None if unrecognized."""
    if not v:
        return None
    return VERDICT_ALIASES.get(str(v).strip().upper())


def keywords(text):
    """Content keywords of a string: lowercase words, len > 3, minus stopwords."""
    words = re.findall(r"[a-z0-9\-]+", str(text).lower())
    return {w for w in words if len(w) > 3 and w not in STOPWORDS}


def brief_text(brief):
    """All scorable text of an audit brief concatenated."""
    parts = [
        brief.get("summary", ""),
        " ".join(brief.get("cited_policy_rules", []) or []),
        brief.get("reasoning", ""),
    ]
    return " ".join(str(p) for p in parts)


class MockJudge:
    """Rule-based judge. No API key, fully deterministic."""

    name = "mock"

    def score(self, expected, brief):
        exp_verdict = normalize_verdict(expected.get("expected_verdict"))
        got_verdict = normalize_verdict(brief.get("risk_verdict"))
        verdict_match = exp_verdict is not None and exp_verdict == got_verdict

        agent_kw = keywords(brief_text(brief))
        exp_findings = expected.get("expected_findings", []) or []
        recalled, missing = [], []
        for finding in exp_findings:
            kw = keywords(finding)
            if not kw:  # degenerate finding: can't be missed on keywords
                recalled.append(finding)
                continue
            overlap = len(kw & agent_kw) / len(kw)
            (recalled if overlap >= 0.5 else missing).append(finding)

        findings_recall = len(recalled) / len(exp_findings) if exp_findings else 1.0

        notes = []
        if verdict_match:
            notes.append(f"verdict match ({exp_verdict})")
        else:
            notes.append(f"verdict mismatch: expected {exp_verdict}, got {got_verdict}")
        notes.append(f"{len(recalled)}/{len(exp_findings)} findings recalled")
        if missing:
            notes.append("missing: " + "; ".join(missing))
        return {
            "verdict_match": verdict_match,
            "findings_recall": round(findings_recall, 3),
            "notes": ". ".join(notes),
        }


JUDGE_SYSTEM_PROMPT = """You are an impartial grading judge for an invoice-compliance AI agent.
You are given the EXPECTED correct audit outcome and the AGENT's audit brief.
Score the agent strictly on the evidence in its brief.

Return ONLY a JSON object with exactly these keys:
- "verdict_match": true if the agent's risk_verdict equals the expected verdict, else false
- "findings_recall": float 0.0-1.0, fraction of the expected findings that the agent's
  brief actually mentions (summary, cited rules, or reasoning). 1.0 = all mentioned,
  0.0 = none mentioned. Give partial credit per finding.
- "notes": one short sentence explaining the score.

Verdict vocabulary is APPROVE, FLAG, REJECT (treat APPROVED/FLAGGED/REJECTED as the same).
Do not add any text outside the JSON object."""


class GeminiJudge:
    """LLM judge via a raw Gemini API call (dependency-light: stdlib + requests)."""

    name = "gemini"

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "GeminiJudge needs GEMINI_API_KEY (or pass api_key=). "
                "Use JUDGE_MODE=mock for the credential-free judge."
            )
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    def _call(self, user_text):
        url = GEMINI_API_URL_TMPL.format(model=self.model) + f"?key={self.api_key}"
        payload = {
            "contents": [
                {"parts": [{"text": JUDGE_SYSTEM_PROMPT}, {"text": user_text}]}
            ],
            "generationConfig": {"response_mime_type": "application/json"},
        }
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    @staticmethod
    def _extract_json(text):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"judge returned non-JSON: {text[:200]}")
        return json.loads(m.group(0))

    def score(self, expected, brief):
        user_text = (
            "EXPECTED outcome:\n"
            f"verdict: {expected.get('expected_verdict')}\n"
            f"findings: {json.dumps(expected.get('expected_findings', []))}\n\n"
            "AGENT brief:\n" + json.dumps(brief, indent=2)
        )
        try:
            raw = self._call(user_text)
            parsed = self._extract_json(raw)
            return {
                "verdict_match": bool(parsed.get("verdict_match", False)),
                "findings_recall": float(parsed.get("findings_recall", 0.0)),
                "notes": str(parsed.get("notes", ""))[:500],
            }
        except Exception as e:  # never let one bad judge call kill a whole eval run
            return {
                "verdict_match": False,
                "findings_recall": 0.0,
                "notes": f"judge call failed: {e}",
            }


def get_judge():
    """Factory: JUDGE_MODE=mock (default) | gemini."""
    mode = os.getenv("JUDGE_MODE", "mock").strip().lower()
    if mode == "gemini":
        return GeminiJudge()
    if mode == "mock":
        return MockJudge()
    raise ValueError(f"unknown JUDGE_MODE={mode!r} (expected 'mock' or 'gemini')")


def selftest():
    """Smoke-test the MockJudge on a toy example. Run: python judge.py --selftest"""
    expected = {
        "expected_verdict": "FLAG",
        "expected_findings": [
            "duplicate of INV-2025-0042133",
            "same vendor amount and date as prior invoice",
        ],
    }
    good = {
        "risk_verdict": "FLAGGED",
        "summary": "Duplicate of INV-2025-0042133 detected.",
        "cited_policy_rules": ["FIN-2.1: Duplicate Invoice Detection"],
        "reasoning": "Same vendor amount and date as prior invoice INV-2025-0042133; billed twice.",
    }
    bad = {
        "risk_verdict": "APPROVE",
        "summary": "Looks fine.",
        "cited_policy_rules": [],
        "reasoning": "No issues found.",
    }
    j = MockJudge()
    r1, r2 = j.score(expected, good), j.score(expected, bad)
    print("good brief ->", r1)
    print("bad brief  ->", r2)
    assert r1["verdict_match"] and r1["findings_recall"] == 1.0
    assert not r2["verdict_match"] and r2["findings_recall"] == 0.0
    print("MockJudge selftest OK")


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        selftest()
    else:
        print("usage: python judge.py --selftest")
