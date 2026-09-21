"""Retrieval-tool degradation tests.

Both pgvector tools must return {"available": False, ...} — never raise —
when embeddings or the database are unreachable. The DB and embedding
layers are monkeypatched; no live Postgres, no model download.
"""
from __future__ import annotations

from core.agent import retrieval
from core.agent.retrieval import (
    _invoice_query_text,
    find_similar_invoices,
    retrieve_policy,
)


class _DeadDB:
    def connect(self, *a, **k):
        raise ConnectionError("postgres is down (test)")


def _patch_dead_db(monkeypatch):
    monkeypatch.setattr(retrieval, "_engine", _DeadDB())
    monkeypatch.setattr(
        retrieval, "_embed_text", lambda text: [0.0] * 384
    )


class TestRetrievalDegradation:
    def test_similar_invoices_db_down(self, monkeypatch, clean_invoice):
        _patch_dead_db(monkeypatch)
        out = find_similar_invoices(clean_invoice)
        assert out["available"] is False
        assert "database unavailable" in out["error"]
        assert out["results"] == []

    def test_retrieve_policy_db_down(self, monkeypatch, clean_invoice):
        _patch_dead_db(monkeypatch)
        out = retrieve_policy(clean_invoice)
        assert out["available"] is False
        assert "database unavailable" in out["error"]
        assert "policies" not in out

    def test_embedding_failure_degrades(self, monkeypatch, clean_invoice):
        def _boom(text):
            raise RuntimeError("no weights (test)")

        monkeypatch.setattr(retrieval, "_embed_text", _boom)
        out = find_similar_invoices(clean_invoice)
        assert out["available"] is False
        assert "embedding unavailable" in out["error"]

    def test_degraded_result_never_raises(self, monkeypatch, clean_invoice):
        """Even pathological inputs degrade instead of raising."""
        _patch_dead_db(monkeypatch)
        out = find_similar_invoices({})
        assert out["available"] is False


class TestInvoiceQueryText:
    def test_raw_text_preferred(self):
        inv = {"invoice_id": "INV-X", "raw_text": "raw ocr text here"}
        assert _invoice_query_text(inv) == "raw ocr text here"

    def test_fallback_builds_searchable_text(self, clean_invoice):
        text = _invoice_query_text(clean_invoice)
        assert "INV-T1" in text
        assert "V-T1" in text
        assert "1080.0" in text
        assert "widgets" in text
