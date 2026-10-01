"""ProcureAI audit agent: a LangGraph ReAct agent over deterministic tools.

Graph structure (nodes and edges — the short interview answer):

    ┌──────────┐  conditional   ┌──────┐
    │  reason  │ ──────────────▶ │ act  │
    └──────────┘   ▲             └──────┘
         │        │                   │
         │        └───────────────────┘
         ▼
    ┌──────────┐      ┌─────┐
    │ finalize │ ────▶ │ END │
    └──────────┘      └─────┘

* ``reason`` — the LLM looks at the system prompt + transcript and returns
  exactly one JSON object: either a tool call
  (``{"thought", "action", "action_input"}``) or the final verdict
  (``{"thought", "verdict", "confidence", "findings", "amount_at_risk"}``).
* ``act`` — executes ONE deterministic tool (XGBoost score, arithmetic
  check, duplicate search, PO match, vendor assessment, or — optionally —
  the pgvector retrieval tools) and appends the observation to the
  transcript. The tools are pure functions; the LLM never computes.
* ``finalize`` — normalizes the verdict dict (unknown verdicts become
  FLAG) and attaches the instrumentation summary.
* The conditional edge after ``reason`` routes on the LLM's JSON output:
  verdict present → ``finalize``; step budget exhausted → ``finalize``
  with a forced FLAG ("did not converge"); tool action → ``act``;
  unparseable or empty output → back to ``reason`` with a retry note in
  the transcript.

State carries the audit context (invoice/po/vendor/history), the
append-only transcript, the step-by-step trace (thought/action/
observation), the timed call log, and the step counter. The graph is
compiled once per ``AuditAgent``; ``audit()`` just invokes it.

Unlike the legacy single-prompt auditor, this agent reasons about what
evidence it needs, calls tools, observes results, and only then verdicts.
The LLM reasons; the tools compute. Free-tier Gemini model only
(GEMINI_MODEL env, default gemini-3.1-flash-lite).
"""
from __future__ import annotations

import json
import operator
import os
import re
import sys
import time
from typing import Annotated, Any, Dict, List, Optional, TypedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from langgraph.graph import END, StateGraph

from core.agent.tools import TOOLS, tool_descriptions
from core.agent.llm_throttle import GeminiClient, redact

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
# Note: free-tier enforcement happens inside GeminiClient.__init__ (via
# llm_throttle.assert_free_tier) so that misconfiguration fails with a clean
# config error instead of at import time.
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "8"))

SYSTEM_PROMPT = """You are a procurement fraud audit agent. You investigate ONE invoice
at a time using the tools below. Think step by step.

Rules:
- ALWAYS call verify_arithmetic first — arithmetic certainty beats ML suspicion.
- Then call ALL remaining tools before verdicting: score_invoice_xgb,
  find_duplicates, check_po, assess_vendor. A verdict with missing evidence
  is a guess; run the full sweep every time.
- Never invent numbers: cite only values returned by tools.
- After at most {max_steps} tool calls, give the final verdict.

Tools:
{tools}

Respond in exactly one of these two JSON shapes, no other text:

To call a tool:
{{"thought": "<why this tool next>", "action": "<tool_name>", "action_input": {{<args as JSON>}} }}

For the final verdict (no more tools needed):
{{"thought": "<summary of evidence>", "verdict": "APPROVE|FLAG|REJECT",
  "confidence": <0-1>, "findings": ["<evidence-backed finding>", ...],
  "amount_at_risk": <number>}}

Findings must explicitly address each of these (one finding per line):
- whether line totals reconcile with subtotal and total (cite the numbers)
- whether the total is within the PO amount limit (cite billed vs limit)
- vendor standing (risk rating, history, ghost signals if any)
- duplicates found or explicitly none
- XGBoost anomaly score and what it means

Verdict guidance — apply in this order, first match wins:
- REJECT (certain fraud, do not downgrade to FLAG):
  * verify_arithmetic reports ok=false — ANY mismatch (line totals vs subtotal,
    tax vs rate, subtotal+tax vs total). The tool is decisive; never overrule it.
  * assess_vendor reports the vendor is NOT in the approved vendor master or the
    tax ID is UNVERIFIED — an unregistered vendor is certain fraud. This is
    decisive: ghost signals are REJECT, never a mere FLAG. Do not downgrade an
    unregistered vendor to FLAG for any reason.
  * check_po reports billed over the PO limit with no justification.
  * Near-threshold totals are NEVER REJECT by themselves — that is a FLAG.
- FLAG (suspicious, needs human review):
  * find_duplicates reports a near-duplicate: the match is heuristic (amount
    within 2%, date within 45 days), so cite the matched invoice ID and FLAG
    for human review — do NOT reject on a fuzzy match.
  * check_po reports near_10k_threshold=true (just below the $10,000 approval
    threshold), with or without a possible paired invoice (possible split
    billing) — cite the amounts and the threshold. Split-billing suspicion is
    FLAG, never REJECT.
  * check_po reports price_drift_lines non-empty (unit price >10% above the PO
    contracted rate) — cite the line item and the percentage.
  * XGBoost anomaly score is high (>=0.7) but no tool above corroborates it.
  * Vendor is on file but has thin history plus a high risk rating (>=0.5).
- APPROVE (hard rule, overrides everything below it): verify_arithmetic ok=true
  AND vendor is on file with a verified tax ID AND no duplicates found AND total
  is within the PO limit → APPROVE. This overrides the XGBoost score and any
  vague suspicion. Do NOT flag a clean invoice on the ML score alone. Thin vendor
  history with a low or moderate risk rating (<0.5) is informational, not
  suspicious — when the four conditions above hold, APPROVE even if the vendor's
  history is thin. Thin history only supports FLAG when the risk rating is high
  (>=0.5) or another tool corroborates the suspicion.

Findings vocabulary — state concrete, checkable facts using these exact terms
so the audit brief is unambiguous:
- arithmetic: "line totals reconcile with subtotal" or cite the exact mismatch.
- PO: "total within PO amount limit" (cite billed vs limit), or "just below
  $10,000 approval threshold" / "possible split billing with INV-<id>".
- vendor: "vendor on approved vendor list" or "vendor not in approved vendor
  master" / "unregistered vendor with unverified tax ID".
- duplicates: "duplicate of INV-<id>" or "no duplicates found".
- price: "unit price <PCT>% above PO contracted rate" naming the line item.
- ML: "XGBoost anomaly score <s> (<risk_level>)".
One finding per checklist bullet; every finding must cite a tool observation.
"""


def _extract_json(text: str) -> Dict[str, Any]:
    """Pull the first {...} JSON object out of model output."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in model output: {text[:200]}")
    return json.loads(match.group(0))


class _AuditState(TypedDict):
    """LangGraph state for one invoice audit."""

    context: Dict[str, Any]            # invoice / po / vendor / history
    system: str                       # rendered system prompt
    transcript: Annotated[List[str], operator.add]   # append-only LLM context
    trace: List[Dict[str, Any]]       # step-by-step thought/action/observation
    call_log: Annotated[List[Dict[str, Any]], operator.add]  # timed instrumentation
    step: int                         # reason iterations consumed
    verdict_msg: Optional[Dict[str, Any]]  # set when the LLM verdicts
    pending_action: Optional[str]     # tool chosen by the last reason step
    pending_input: Dict[str, Any]     # its arguments
    pending_step: int                 # reason step that requested the tool
    audit_t0: float                   # wall-clock start for instrumentation
    result: Optional[Dict[str, Any]]  # filled by finalize


class AuditAgent:
    """LangGraph ReAct audit agent.

    Live mode needs GEMINI_API_KEY (free-tier model only, enforced by
    llm_throttle). Pass ``llm_fn`` to inject a deterministic backend instead
    (used by the eval harness's --mock-llm dry-run; never a real evaluation).
    """

    def __init__(self, api_key: str | None = None, max_steps: int = MAX_STEPS,
                 llm_fn=None):
        self.max_steps = max_steps
        self.trace: List[Dict[str, Any]] = []
        # Per-call instrumentation, reset on every audit() call (the eval
        # harness reuses one agent across invoices).
        self.call_log: List[Dict[str, Any]] = []
        self._audit_t0: float = 0.0
        if llm_fn is not None:
            self._llm = llm_fn
            self.model = "mock-llm (dry-run, not a real evaluation)"
        else:
            self._client = GeminiClient(api_key=api_key, model=GEMINI_MODEL)
            self._llm = self._client.generate
            self.model = self._client.model
        self._graph = self._build_graph()

    # ------------------------------------------------------------------
    # LangGraph construction
    # ------------------------------------------------------------------
    def _build_graph(self):
        """Compile the reason -> act -> reason loop with a finalize exit."""
        graph = StateGraph(_AuditState)
        graph.add_node("reason", self._node_reason)
        graph.add_node("act", self._node_act)
        graph.add_node("finalize", self._node_finalize)
        graph.set_entry_point("reason")
        graph.add_conditional_edges(
            "reason",
            self._route_after_reason,
            {"act": "act", "reason": "reason", "finalize": "finalize"},
        )
        graph.add_edge("act", "reason")
        graph.add_edge("finalize", END)
        return graph.compile()

    def _route_after_reason(self, state: _AuditState) -> str:
        """Route on the LLM's JSON output: verdict, tool call, or retry."""
        msg = state.get("verdict_msg")
        if msg and "verdict" in msg:
            return "finalize"
        if state["step"] >= self.max_steps:
            return "finalize"  # step budget exhausted -> forced FLAG
        if state.get("pending_action"):
            return "act"
        return "reason"  # unparseable / empty output: retry with a note

    # ------------------------------------------------------------------
    # Graph nodes
    # ------------------------------------------------------------------
    def _node_reason(self, state: _AuditState) -> Dict[str, Any]:
        """One LLM reasoning step: emit a tool call or the final verdict."""
        step = state["step"]
        prompt = state["system"] + "\n\n" + "\n\n".join(state["transcript"])
        if step == self.max_steps - 1:
            prompt += ("\n\nThis is your LAST step. You MUST return the final "
                       "verdict JSON now (no action).")
        t_llm = time.perf_counter()
        raw = self._redacted_llm(prompt)
        llm_latency = time.perf_counter() - t_llm
        llm_entry = {
            "kind": "llm",
            "step": step,
            "latency_s": round(llm_latency, 4),
            "parsed": True,
            "tokens": self._consume_usage(),
        }
        try:
            msg = _extract_json(raw)
        except ValueError:
            llm_entry["parsed"] = False
            return {
                "transcript": [f"Agent output (unparseable, retry): {raw[:300]}"],
                "call_log": [llm_entry],
                "step": step + 1,
            }
        trace_entry = {
            "step": step,
            "thought": msg.get("thought"),
            "action": msg.get("action"),
        }
        update: Dict[str, Any] = {
            "call_log": [llm_entry],
            "trace": state["trace"] + [trace_entry],
            "step": step + 1,
        }
        if "verdict" in msg:
            update["verdict_msg"] = msg
            return update
        action = msg.get("action")
        if not action:
            update["transcript"] = ["No action or verdict given; provide one."]
            return update
        update["pending_action"] = action
        update["pending_input"] = msg.get("action_input", {}) or {}
        update["pending_step"] = step
        return update

    def _node_act(self, state: _AuditState) -> Dict[str, Any]:
        """Execute the chosen deterministic tool; record the observation."""
        action = state["pending_action"]
        t0 = time.perf_counter()
        obs = self._dispatch(
            action, state.get("pending_input") or {}, state["context"])
        tool_entry = {
            "kind": "tool",
            "step": state["pending_step"],
            "name": action,
            "latency_s": round(time.perf_counter() - t0, 4),
            "error": bool(isinstance(obs, dict) and obs.get("error")),
        }
        trace = [dict(e) for e in state["trace"]]
        trace[-1]["observation"] = obs
        return {
            "trace": trace,
            "call_log": [tool_entry],
            "transcript": [
                f"Thought: {trace[-1].get('thought')}\nAction: {action}\n"
                f"Observation: {json.dumps(obs, default=str)[:1500]}"
            ],
            "pending_action": None,
            "pending_input": {},
        }

    def _node_finalize(self, state: _AuditState) -> Dict[str, Any]:
        """Normalize the verdict and attach the instrumentation summary."""
        # Back-compat: the eval harness and tests read these attributes.
        self.call_log = list(state["call_log"])
        self._audit_t0 = state["audit_t0"]
        self.trace = [dict(e) for e in state["trace"]]
        msg = state.get("verdict_msg")
        if msg and "verdict" in msg:
            result = self._finalize(msg)
        else:
            invoice = state["context"]["invoice"]
            result = self._finalize({
                "verdict": "FLAG",
                "confidence": 0.5,
                "findings": ["agent did not converge within step budget; "
                             "needs human review"],
                "amount_at_risk": float(invoice.get("total_amount", 0)),
            })
        return {"result": result}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def audit(self, invoice: Dict[str, Any], po: Dict[str, Any] | None = None,
              vendor: Dict[str, Any] | None = None,
              history: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
        """Run the LangGraph ReAct loop and return the final verdict dict."""
        context = {"invoice": invoice, "po": po or {},
                   "vendor": vendor or {}, "history": history or []}
        self.trace = []
        self.call_log = []
        self._audit_t0 = time.perf_counter()
        initial: _AuditState = {
            "context": context,
            "system": SYSTEM_PROMPT.format(max_steps=self.max_steps,
                                           tools=tool_descriptions()),
            "transcript": [
                f"Invoice under audit:\n{json.dumps(invoice, indent=2, default=str)}"
            ],
            "trace": [],
            "call_log": [],
            "step": 0,
            "verdict_msg": None,
            "pending_action": None,
            "pending_input": {},
            "pending_step": 0,
            "audit_t0": self._audit_t0,
            "result": None,
        }
        final = self._graph.invoke(
            initial,
            config={"recursion_limit": self.max_steps * 4 + 10},
        )
        self.trace = [dict(e) for e in final["trace"]]
        self.call_log = list(final["call_log"])
        return final["result"]

    # ------------------------------------------------------------------
    # Helpers (unchanged from the hand-rolled loop)
    # ------------------------------------------------------------------
    def _consume_usage(self) -> Optional[Dict[str, Optional[int]]]:
        """Pop the last LLM call's token usage, if the backend captured any.

        The live GeminiClient stores usageMetadata per call; the mock LLM
        backend has none -> None, recorded honestly as missing.
        """
        client = getattr(self, "_client", None)
        usage = getattr(client, "last_usage", None)
        if client is not None:
            client.last_usage = None  # consume: one usage record per call
        return usage

    def _build_trace(self) -> Dict[str, Any]:
        """Summarize per-call instrumentation for this audit."""
        llm_calls = [c for c in self.call_log if c["kind"] == "llm"]
        tool_calls = [c for c in self.call_log if c["kind"] == "tool"]
        prompt_toks = cand_toks = total_toks = 0
        with_usage = 0
        for c in llm_calls:
            tok = c.get("tokens")
            if tok and tok.get("total_tokens") is not None:
                with_usage += 1
                prompt_toks += tok.get("prompt_tokens") or 0
                cand_toks += tok.get("candidates_tokens") or 0
                total_toks += tok.get("total_tokens")
        wall = (time.perf_counter() - self._audit_t0) if self._audit_t0 else 0.0
        return {
            "audit_wall_s": round(wall, 3),
            "llm_calls": len(llm_calls),
            "tool_calls": len(tool_calls),
            "llm_latency_s": round(sum(c["latency_s"] for c in llm_calls), 4),
            "tool_latency_s": round(sum(c["latency_s"] for c in tool_calls), 4),
            "tokens": {
                "prompt_tokens": prompt_toks,
                "candidates_tokens": cand_toks,
                "total_tokens": total_toks,
                "llm_calls_with_usage": with_usage,
                "llm_calls_missing_usage": len(llm_calls) - with_usage,
            },
            "cost_usd": 0.0,
            "cost_note": (
                "Free-tier Gemini model (allowlist enforced in "
                "llm_throttle.py); $0 real spend. Raw token counts are "
                "reported so a paid-tier cost estimate can be computed later."
            ),
            "calls": self.call_log,
        }

    def _dispatch(self, action: str, action_input: Dict[str, Any],
                  context: Dict[str, Any]) -> Any:
        """Run a tool by name. history/po/vendor come from the audit context."""
        if action not in TOOLS:
            return {"error": f"unknown tool: {action}"}
        fn, _ = TOOLS[action]
        try:
            if action == "find_duplicates":
                return fn(context["invoice"], context.get("history", []))
            if action == "check_po":
                return fn(context["invoice"], context.get("po", {}))
            if action == "assess_vendor":
                return fn(context.get("vendor", {}), context.get("history", []))
            if action == "score_invoice_xgb":
                # The XGBoost tool needs amount_limit (from the PO) and
                # risk_rating (from the vendor master) — enrich the invoice
                # dict with the audit context so it scores on real features.
                enriched = dict(context["invoice"])
                enriched.setdefault("amount_limit",
                                    context.get("po", {}).get("amount_limit", 0))
                enriched.setdefault("risk_rating",
                                    context.get("vendor", {}).get("risk_rating", 0))
                return fn(enriched)
            return fn(context["invoice"])
        except Exception as e:  # tools must never crash the loop
            return {"error": f"{action} failed: {e}"}

    def _redacted_llm(self, prompt: str) -> str:
        """Call the LLM backend; any failure surfaces with secrets redacted."""
        try:
            return self._llm(prompt)
        except Exception as e:  # noqa: BLE001 - redacted, then re-raised
            key = getattr(self, "_client", None)
            raise RuntimeError(
                redact(e, key.api_key if key else None)
            ) from e

    def _finalize(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        verdict = str(msg.get("verdict", "FLAG")).upper()
        if verdict not in ("APPROVE", "FLAG", "REJECT"):
            verdict = "FLAG"
        return {
            "verdict": verdict,
            "confidence": float(msg.get("confidence", 0.5)),
            "findings": list(msg.get("findings", [])),
            "amount_at_risk": float(msg.get("amount_at_risk", 0)),
            "steps": len(self.trace),
            "model": self.model,
            "trace": self._build_trace(),
        }
