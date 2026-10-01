"""Shared fixtures for the ProcureAI pytest suite.

Conventions:
- No live Gemini calls, ever. Every test that touches the agent injects a
  scripted ``llm_fn`` (queue_llm) or monkeypatches the backend.
- No live Postgres. Retrieval-tool tests monkeypatch the embedding and DB
  layers and assert the graceful-degradation contract.
- The XGBoost engine is trained once per session (``xgb_warmed``) on the
  same 20K-row synthetic distribution as the proofs; tests that need a real
  score request it explicitly.
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


# ---------------------------------------------------------------------------
# Fixture invoices
# ---------------------------------------------------------------------------

def _line(desc="widgets", qty=10, unit=100.0, po_unit=100.0):
    return {
        "description": desc,
        "quantity": qty,
        "unit_price": unit,
        "line_total": round(qty * unit, 2),
        "po_unit_price": po_unit,
    }


@pytest.fixture
def clean_invoice():
    """Arithmetic-clean invoice: subtotal + 8% tax == total, lines reconcile."""
    return {
        "invoice_id": "INV-T1",
        "vendor_id": "V-T1",
        "po_id": "PO-T1",
        "subtotal": 1000.00,
        "tax_amount": 80.00,
        "total_amount": 1080.00,
        "invoice_date": "2026-01-15",
        "line_items": [_line()],
    }


@pytest.fixture
def clean_po():
    return {"po_id": "PO-T1", "amount_limit": 5000.00}


@pytest.fixture
def clean_vendor():
    return {
        "vendor_id": "V-T1",
        "vendor_name": "Acme Corp",
        "risk_rating": 0.2,
        "on_file": True,
        "tax_id": "TAX-123",
    }


@pytest.fixture
def vendor_history(clean_vendor):
    """Three prior APPROVED invoices: thick enough history to avoid the
    thin-history ghost signal."""
    return [
        {
            "invoice_id": f"INV-H{i}",
            "vendor_id": clean_vendor["vendor_id"],
            "po_id": "PO-T1",
            "total_amount": 900.0 + i,
            "invoice_date": "2025-12-0%d" % (i + 1),
            "status": "APPROVED",
        }
        for i in range(3)
    ]


# ---------------------------------------------------------------------------
# Scripted mock LLM
# ---------------------------------------------------------------------------

def act(name: str) -> str:
    return json.dumps(
        {"thought": f"test: call {name}", "action": name, "action_input": {}}
    )


def final_verdict(
    verdict: str = "APPROVE",
    confidence: float = 0.9,
    findings: list | None = None,
    amount_at_risk: float = 0.0,
) -> str:
    return json.dumps(
        {
            "thought": "test: done",
            "verdict": verdict,
            "confidence": confidence,
            "findings": findings or ["test finding"],
            "amount_at_risk": amount_at_risk,
        }
    )


def queue_llm(responses: list):
    """Deterministic LLM backend: pops scripted responses in order.

    If the agent asks for more steps than scripted, it gets a FLAG verdict
    so the loop always terminates instead of raising StopIteration.
    """
    it = iter(responses)

    def _fn(prompt: str) -> str:  # noqa: ARG001 - prompt ignored by design
        try:
            return next(it)
        except StopIteration:
            return final_verdict("FLAG", 0.5, ["script exhausted"], 0.0)

    return _fn


@pytest.fixture
def sweep_llm():
    """Mock that runs the full 5-tool deterministic sweep, then APPROVEs."""
    return queue_llm(
        [
            act("verify_arithmetic"),
            act("score_invoice_xgb"),
            act("find_duplicates"),
            act("check_po"),
            act("assess_vendor"),
            final_verdict("APPROVE", 0.95, ["clean on all tools"], 0.0),
        ]
    )


# ---------------------------------------------------------------------------
# XGBoost warmup (once per session)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def xgb_warmed():
    """Train the tool-shared XGBoost engine once (~10s, 20K synthetic rows).

    Mirrors api.main._ensure_agent_xgb_trained: same distribution as the
    proofs. Tests asserting real scores must request this fixture.
    """
    from api.main import _ensure_agent_xgb_trained

    _ensure_agent_xgb_trained()
    from core.agent import tools as agent_tools

    assert agent_tools._get_engine().model is not None
    return True


@pytest.fixture
def invoice_copy(clean_invoice):
    return copy.deepcopy(clean_invoice)
