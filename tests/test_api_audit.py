"""API contract tests for POST /audit.

Pins the honesty contract: when the LLM backend is unreachable the route
returns 503 with an error — never a fabricated verdict. The success and
degraded paths run through run_agent_audit with a scripted llm_fn (no
Gemini quota).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app, run_agent_audit
from core.agent import retrieval
from tests.conftest import act, final_verdict, queue_llm

client = TestClient(app)

SWEEP = [
    "verify_arithmetic",
    "score_invoice_xgb",
    "find_duplicates",
    "check_po",
    "assess_vendor",
]

MIN_INVOICE = {
    "invoice_id": "INV-API1",
    "subtotal": 1000.0,
    "tax_amount": 80.0,
    "total_amount": 1080.0,
}


def _payload(**overrides):
    body = {"invoice": dict(MIN_INVOICE)}
    body.update(overrides)
    return body


class TestHealth:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"


class TestAuditHonestFailure:
    def test_no_api_key_returns_503_not_a_verdict(
        self, monkeypatch, xgb_warmed
    ):
        """Without GEMINI_API_KEY the agent cannot run: 503, no verdict."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        r = client.post("/audit", json=_payload())
        assert r.status_code == 503
        body = r.json()
        assert "audit unavailable" in body["detail"]
        assert "verdict" not in body


class TestRunAgentAudit:
    def _ctx(self, clean_invoice, clean_po, clean_vendor, vendor_history):
        return dict(
            invoice=clean_invoice,
            po=clean_po,
            vendor=clean_vendor,
            history=vendor_history,
        )

    def test_success_shape(
        self, xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history
    ):
        llm = queue_llm([act(n) for n in SWEEP]
                        + [final_verdict("APPROVE", 0.95, ["clean"], 0.0)])
        out = run_agent_audit(llm_fn=llm, **self._ctx(
            clean_invoice, clean_po, clean_vendor, vendor_history))
        assert out["verdict"] == "APPROVE"
        assert out["invoice_id"] == "INV-T1"
        assert out["degraded"] is False
        assert out["unavailable_tools"] == []
        assert out["tool_errors"] == []
        assert out["policy_citations"] == []
        assert set(out["tool_results"]) == set(SWEEP)
        assert "mock-llm" in out["model"]
        trace = out["trace"]
        assert trace["llm_calls"] == 6
        assert trace["tool_calls"] == 5
        assert trace["cost_usd"] == 0.0
        assert "note" in out

    def test_retrieval_unavailable_marks_degraded(
        self, monkeypatch, xgb_warmed, clean_invoice, clean_po, clean_vendor,
        vendor_history,
    ):
        """A retrieval tool with no DB degrades the audit instead of
        crashing it: verdict still produced, degraded=True, tool named."""
        def _boom(text):
            raise RuntimeError("embedding down (test)")

        monkeypatch.setattr(retrieval, "_embed_text", _boom)
        llm = queue_llm(
            [act("verify_arithmetic"), act("find_similar_invoices"),
             final_verdict("FLAG", 0.6, ["needs review"], 1080.0)]
        )
        out = run_agent_audit(llm_fn=llm, **self._ctx(
            clean_invoice, clean_po, clean_vendor, vendor_history))
        assert out["verdict"] == "FLAG"
        assert out["degraded"] is True
        assert "find_similar_invoices" in out["unavailable_tools"]
        assert out["tool_results"]["find_similar_invoices"]["available"] is False

    def test_llm_failure_raises_honest_error(
        self, xgb_warmed, clean_invoice, clean_po, clean_vendor, vendor_history
    ):
        def _dead(prompt):
            raise ConnectionError("gemini unreachable (test)")

        with pytest.raises(RuntimeError, match="audit failed"):
            run_agent_audit(llm_fn=_dead, **self._ctx(
                clean_invoice, clean_po, clean_vendor, vendor_history))

    def test_tool_error_recorded_not_hidden(
        self, monkeypatch, xgb_warmed, clean_invoice, clean_po, clean_vendor,
        vendor_history,
    ):
        """A tool that raises is recorded in tool_errors and marks the
        audit degraded — evidence gaps are visible, not silent."""
        from core.agent import tools as agent_tools

        def _boom(invoice):
            raise RuntimeError("arithmetic exploded (test)")

        monkeypatch.setitem(
            agent_tools.TOOLS, "verify_arithmetic", (_boom, "boom"))
        llm = queue_llm([act("verify_arithmetic"),
                         final_verdict("FLAG", 0.5, ["gap"], 1080.0)])
        out = run_agent_audit(llm_fn=llm, **self._ctx(
            clean_invoice, clean_po, clean_vendor, vendor_history))
        assert out["degraded"] is True
        assert any("verify_arithmetic" in e for e in out["tool_errors"])
