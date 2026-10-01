"""Agent dispatch, trace instrumentation, and convergence tests.

All use a scripted llm_fn — no Gemini calls. The trace tests pin the
honesty contract: every LLM call and tool call is timed, token usage is
captured when the backend provides it and honestly counted as missing when
it doesn't, and cost is always 0.0 with the free-tier note.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.agent import tools as agent_tools
from core.agent.agentic_auditor import AuditAgent
from core.agent.tools import TOOLS as TOOL_REGISTRY
from tests.conftest import act, final_verdict, queue_llm


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_unknown_tool_returns_error_dict(self, clean_invoice):
        agent = AuditAgent(llm_fn=lambda prompt: final_verdict())
        out = agent._dispatch("not_a_tool", {}, {"invoice": clean_invoice})
        assert out == {"error": "unknown tool: not_a_tool"}

    def test_tool_exception_does_not_crash_loop(self, monkeypatch, clean_invoice):
        def _boom(invoice):
            raise RuntimeError("deterministic failure")

        monkeypatch.setitem(TOOL_REGISTRY, "verify_arithmetic", (_boom, "boom"))
        agent = AuditAgent(llm_fn=lambda prompt: final_verdict())
        out = agent._dispatch(
            "verify_arithmetic", {}, {"invoice": clean_invoice}
        )
        assert "error" in out
        assert "verify_arithmetic failed" in out["error"]

    def test_find_duplicates_gets_history(self, clean_invoice):
        agent = AuditAgent(llm_fn=lambda prompt: final_verdict())
        hist = [
            {
                "invoice_id": "INV-OLD",
                "vendor_id": "V-T1",
                "po_id": "PO-T1",
                "total_amount": 1080.0,
                "invoice_date": "2026-01-10",
            }
        ]
        out = agent._dispatch(
            "find_duplicates", {}, {"invoice": clean_invoice, "history": hist}
        )
        assert out["duplicate_found"] is True

    def test_check_po_gets_po(self, clean_invoice):
        agent = AuditAgent(llm_fn=lambda prompt: final_verdict())
        out = agent._dispatch(
            "check_po",
            {},
            {
                "invoice": clean_invoice,
                "po": {"po_id": "PO-T1", "amount_limit": 500.0},
            },
        )
        assert out["over_limit"] is True

    def test_assess_vendor_gets_vendor_and_history(
        self, clean_invoice, clean_vendor, vendor_history
    ):
        agent = AuditAgent(llm_fn=lambda prompt: final_verdict())
        out = agent._dispatch(
            "assess_vendor",
            {},
            {
                "invoice": clean_invoice,
                "vendor": clean_vendor,
                "history": vendor_history,
            },
        )
        assert out["vendor_id"] == "V-T1"
        assert out["prior_invoices"] == 3


# ---------------------------------------------------------------------------
# Trace instrumentation
# ---------------------------------------------------------------------------

SWEEP = [
    "verify_arithmetic",
    "score_invoice_xgb",
    "find_duplicates",
    "check_po",
    "assess_vendor",
]


def _run_sweep(xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history):
    agent = AuditAgent(
        llm_fn=queue_llm([act(n) for n in SWEEP] + [final_verdict()])
    )
    out = agent.audit(
        clean_invoice, po=clean_po, vendor=clean_vendor, history=vendor_history
    )
    return agent, out


class TestTrace:
    def test_sweep_trace_counts(
        self, xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history
    ):
        agent, out = _run_sweep(
            xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history
        )
        trace = out["trace"]
        assert out["steps"] == 6  # 5 tool steps + 1 verdict step (all logged)
        assert trace["llm_calls"] == 6  # 5 tool steps + 1 verdict step
        assert trace["tool_calls"] == 5
        assert trace["audit_wall_s"] > 0
        assert trace["llm_latency_s"] >= 0
        assert trace["tool_latency_s"] >= 0

    def test_missing_token_usage_counted_honestly(
        self, xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history
    ):
        """The mock backend provides no usageMetadata -> every call counted
        as missing, never invented."""
        _, out = _run_sweep(
            xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history
        )
        toks = out["trace"]["tokens"]
        assert toks["llm_calls_with_usage"] == 0
        assert toks["llm_calls_missing_usage"] == 6
        assert toks["total_tokens"] == 0

    def test_usage_captured_when_backend_provides_it(self, clean_invoice):
        agent = AuditAgent(llm_fn=lambda prompt: final_verdict("APPROVE"))
        agent._client = SimpleNamespace(
            last_usage={
                "prompt_tokens": 100,
                "candidates_tokens": 20,
                "total_tokens": 120,
            }
        )
        out = agent.audit(clean_invoice)
        toks = out["trace"]["tokens"]
        assert toks["llm_calls_with_usage"] == 1
        assert toks["llm_calls_missing_usage"] == 0
        assert toks["prompt_tokens"] == 100
        assert toks["candidates_tokens"] == 20
        assert toks["total_tokens"] == 120

    def test_cost_always_zero_with_free_tier_note(
        self, xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history
    ):
        _, out = _run_sweep(
            xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history
        )
        assert out["trace"]["cost_usd"] == 0.0
        assert "Free-tier" in out["trace"]["cost_note"]

    def test_call_log_entry_shape(
        self, xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history
    ):
        agent, _ = _run_sweep(
            xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history
        )
        for entry in agent.call_log:
            assert entry["kind"] in ("llm", "tool")
            assert isinstance(entry["step"], int)
            assert entry["latency_s"] >= 0
        tool_names = [e["name"] for e in agent.call_log if e["kind"] == "tool"]
        assert tool_names == SWEEP
        llm_entries = [e for e in agent.call_log if e["kind"] == "llm"]
        assert all(e["parsed"] is True for e in llm_entries)
        assert all(e["tokens"] is None for e in llm_entries)

    def test_unparseable_output_retries_and_is_logged(self, clean_invoice):
        agent = AuditAgent(
            llm_fn=queue_llm(
                ["definitely not json {{{", act("verify_arithmetic"),
                 final_verdict("APPROVE")]
            )
        )
        out = agent.audit(clean_invoice)
        assert out["verdict"] == "APPROVE"
        unparsed = [e for e in agent.call_log
                    if e["kind"] == "llm" and e["parsed"] is False]
        assert len(unparsed) == 1
        # the retry still produced a verdict: 3 llm calls, 1 tool call
        trace = out["trace"]
        assert trace["llm_calls"] == 3
        assert trace["tool_calls"] == 1

    def test_no_convergence_forces_flag(self, clean_invoice):
        agent = AuditAgent(
            max_steps=3,
            llm_fn=queue_llm([act("verify_arithmetic")] * 10),
        )
        out = agent.audit(clean_invoice)
        assert out["verdict"] == "FLAG"
        assert out["confidence"] == 0.5
        assert any("did not converge" in f for f in out["findings"])

    def test_invalid_verdict_normalized_to_flag(self, clean_invoice):
        agent = AuditAgent(llm_fn=lambda prompt: final_verdict("MAYBE"))
        out = agent.audit(clean_invoice)
        assert out["verdict"] == "FLAG"

    def test_verdict_is_uppercased(self, clean_invoice):
        agent = AuditAgent(llm_fn=lambda prompt: final_verdict("reject"))
        out = agent.audit(clean_invoice)
        assert out["verdict"] == "REJECT"
