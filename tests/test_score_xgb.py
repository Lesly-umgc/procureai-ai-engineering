"""score_invoice_xgb tests.

Covers: real trained-model scoring, the risk_level boundary mapping, the
untrained-engine error contract, and the context-enrichment fix (the agent's
_dispatch must supply amount_limit from the PO and risk_rating from the
vendor — the tool raised TypeError on every agent call before this fix).
"""
from __future__ import annotations

from core.agent import tools as agent_tools
from core.agent.agentic_auditor import AuditAgent
from core.agent.tools import score_invoice_xgb
from tests.conftest import final_verdict


class _StubEngine:
    """Minimal engine double: preset score, pretends to be trained."""

    def __init__(self, score: float):
        self.model = object()  # not None -> "trained"
        self._score = score

    def score_invoice(self, **kwargs):
        self.seen_kwargs = kwargs
        return self._score


class TestScoreInvoiceXgb:
    def test_real_model_returns_bounded_score(self, xgb_warmed, clean_invoice):
        inv = dict(clean_invoice, amount_limit=5000.0, risk_rating=0.2)
        out = score_invoice_xgb(inv)
        assert "error" not in out
        assert 0.0 <= out["anomaly_score"] <= 1.0
        assert out["risk_level"] in ("low", "medium", "high")
        # rounded to 4dp
        assert out["anomaly_score"] == round(out["anomaly_score"], 4)

    def test_untrained_engine_returns_error_not_crash(
        self, monkeypatch, clean_invoice
    ):
        class _Untrained:
            model = None

        monkeypatch.setattr(agent_tools, "_engine", _Untrained())
        out = score_invoice_xgb(clean_invoice)
        assert out["anomaly_score"] == 0.0
        assert "error" in out

    def test_risk_level_boundaries(self, monkeypatch, clean_invoice):
        cases = [
            (0.95, "high"),
            (0.70, "high"),  # inclusive boundary
            (0.6999, "medium"),
            (0.40, "medium"),  # inclusive boundary
            (0.3999, "low"),
            (0.0, "low"),
        ]
        for score, expected in cases:
            monkeypatch.setattr(agent_tools, "_engine", _StubEngine(score))
            out = score_invoice_xgb(clean_invoice)
            assert out["risk_level"] == expected, f"score={score}"
            assert out["anomaly_score"] == round(score, 4)

    def test_dispatch_enriches_scorer_context(
        self, monkeypatch, clean_invoice
    ):
        """Regression test: the agent must pass amount_limit (PO) and
        risk_rating (vendor) into the scorer. Before the fix, _dispatch
        passed an unsupported kwarg and the tool raised TypeError on every
        agent call."""
        stub = _StubEngine(0.5)
        monkeypatch.setattr(agent_tools, "_engine", stub)
        agent = AuditAgent(llm_fn=lambda prompt: final_verdict())
        invoice = {
            k: v for k, v in clean_invoice.items() if k != "invoice_id"
        }
        invoice["invoice_id"] = "INV-T1"  # no amount_limit / risk_rating keys
        out = agent._dispatch(
            "score_invoice_xgb",
            {},
            {
                "invoice": invoice,
                "po": {"amount_limit": 7777.0},
                "vendor": {"risk_rating": 0.9},
                "history": [],
            },
        )
        assert "error" not in out
        assert stub.seen_kwargs["amount_limit"] == 7777.0
        assert stub.seen_kwargs["risk_rating"] == 0.9
        assert stub.seen_kwargs["subtotal"] == 1000.0
        assert stub.seen_kwargs["total_amount"] == 1080.0

    def test_dispatch_does_not_override_invoice_values(
        self, monkeypatch, clean_invoice
    ):
        """setdefault semantics: explicit invoice keys win over context."""
        stub = _StubEngine(0.5)
        monkeypatch.setattr(agent_tools, "_engine", stub)
        agent = AuditAgent(llm_fn=lambda prompt: final_verdict())
        invoice = dict(clean_invoice, amount_limit=1111.0)
        agent._dispatch(
            "score_invoice_xgb",
            {},
            {
                "invoice": invoice,
                "po": {"amount_limit": 7777.0},
                "vendor": {"risk_rating": 0.9},
                "history": [],
            },
        )
        assert stub.seen_kwargs["amount_limit"] == 1111.0
