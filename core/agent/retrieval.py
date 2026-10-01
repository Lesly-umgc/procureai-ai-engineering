"""pgvector retrieval tools for the ProcureAI audit agent.

Two tools, both real pgvector similarity searches (no LLM involved):

1. ``find_similar_invoices(invoice, k=5)`` — embeds the invoice under audit
   with the repo's real embedding pipeline (``core/document_ai.py``:
   all-MiniLM-L6-v2, 384-dim, L2-normalized) and returns the k nearest
   historical invoices from the ``invoices`` table by cosine distance, with
   their amounts, dates, and seeded statuses (FLAGGED/APPROVED) so the agent
   can compare ("this invoice looks like previously flagged cases").

2. ``retrieve_policy(invoice, k=3)`` — embeds a query built from the invoice
   and searches the ``policies`` table (seeded from policies/policies.json)
   so the agent can cite the policy section behind its verdict.

Availability: both tools need (a) a reachable Postgres with the pgvector
extension (``DATABASE_URL`` env, same default as database/db.py) and (b) the
``sentence-transformers`` package plus the all-MiniLM-L6-v2 weights. When
either is missing the tools return ``{"available": False, ...}`` instead of
raising — tools must never crash the agent loop.

Honest limitation: bulk-seeded invoice embeddings are random placeholder
vectors (see scripts/synthesize.py::random_embedding), so cosine ranking
over the 250K seed rows is only meaningful once rows are re-embedded with
the real pipeline. The tool reports distances as-is; do not treat a
placeholder-based ranking as evidence of semantic similarity.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import create_engine, select, text

from database.db import Base, Invoice, Policy

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://@localhost:5432/procureai_db")

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL, pool_size=2, max_overflow=0,
            connect_args={"connect_timeout": 5},
        )
    return _engine


def _embed_text(text: str) -> List[float]:
    """Embed text with the repo's real pipeline (all-MiniLM-L6-v2, 384-dim).

    Raises RuntimeError with install instructions if sentence-transformers
    (a document_ai.py dependency) is not installed.
    """
    try:
        from core.document_ai import DocumentAIProcessor
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "embedding unavailable: install the document_ai dependencies "
            "(pip install sentence-transformers) and ensure the "
            "all-MiniLM-L6-v2 model can be downloaded. "
            f"Underlying error: {e}"
        ) from e
    return DocumentAIProcessor().generate_embedding(text or "Empty Invoice")


def _invoice_query_text(invoice: Dict[str, Any]) -> str:
    """Build searchable text from an invoice dict (falls back when raw_text
    is absent, e.g. golden-set invoices)."""
    raw = invoice.get("raw_text")
    if raw:
        return str(raw)
    lines = invoice.get("line_items", []) or []
    descs = ", ".join(str(l.get("description", "")) for l in lines[:8])
    return (
        f"Invoice {invoice.get('invoice_id')} from vendor {invoice.get('vendor_id')} "
        f"against PO {invoice.get('po_id')}, total ${invoice.get('total_amount')}, "
        f"subtotal ${invoice.get('subtotal')}, tax ${invoice.get('tax_amount')}, "
        f"{len(lines)} line items: {descs}"
    )


def _unavailable(reason: str) -> Dict[str, Any]:
    return {"available": False, "error": reason, "results": []}


# ---------------------------------------------------------------------------
# Tool: similar historical invoices (pgvector)
# ---------------------------------------------------------------------------
def find_similar_invoices(invoice: Dict[str, Any], k: int = 5) -> Dict[str, Any]:
    """Find the k most similar historical invoices via pgvector cosine search.

    Returns invoice_number, dates, amounts, seeded status, and cosine distance
    for each hit (lower distance = more similar). Excludes the invoice itself.
    """
    try:
        vec = _embed_text(_invoice_query_text(invoice))
    except Exception as e:  # noqa: BLE001 - any embedding failure degrades gracefully
        return _unavailable(f"embedding unavailable ({type(e).__name__}): {e}")
    try:
        engine = _get_engine()
        inv_id = invoice.get("invoice_id")
        stmt = (
            select(
                Invoice.invoice_number,
                Invoice.invoice_date,
                Invoice.vendor_id,
                Invoice.total_amount,
                Invoice.status,
                Invoice.embedding.cosine_distance(vec).label("distance"),
            )
            .order_by(text("distance"))
            .limit(k + 1)  # +1 in case the invoice itself is in the table
        )
        with engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
    except Exception as e:  # noqa: BLE001 - DB unreachable -> graceful degrade
        return _unavailable(f"database unavailable ({type(e).__name__}): {e}")
    results = []
    for r in rows:
        if inv_id is not None and r["invoice_number"] == inv_id:
            continue
        results.append({
            "invoice_number": r["invoice_number"],
            "invoice_date": str(r["invoice_date"]),
            "vendor_id": r["vendor_id"],
            "total_amount": float(r["total_amount"] or 0),
            "status": r["status"],
            "cosine_distance": round(float(r["distance"]), 4),
        })
        if len(results) >= k:
            break
    return {
        "available": True,
        "query_embedding_dim": len(vec),
        "n": len(results),
        "similar": results,
        "note": (
            "Seeded invoice embeddings are random placeholders "
            "(scripts/synthesize.py); ranking is only semantically meaningful "
            "for rows re-embedded with the real pipeline."
        ),
    }


# ---------------------------------------------------------------------------
# Tool: policy retrieval (pgvector)
# ---------------------------------------------------------------------------
def retrieve_policy(invoice: Dict[str, Any], k: int = 3) -> Dict[str, Any]:
    """Retrieve the k most relevant policy snippets for this invoice via
    pgvector cosine search over the policies table (synthetic seed corpus).

    Returns section code, title, text, and the fraud classes each snippet
    covers, so the agent can cite policy (e.g. "FIN-2.1") in its findings.
    """
    query_text = (
        f"invoice audit: total ${invoice.get('total_amount')}, "
        f"subtotal ${invoice.get('subtotal')}, tax ${invoice.get('tax_amount')}, "
        f"vendor {invoice.get('vendor_id')}, PO {invoice.get('po_id')}. "
        f"{_invoice_query_text(invoice)[:400]}"
    )
    try:
        vec = _embed_text(query_text)
    except Exception as e:  # noqa: BLE001 - any embedding failure degrades gracefully
        return _unavailable(f"embedding unavailable ({type(e).__name__}): {e}")
    try:
        engine = _get_engine()
        stmt = (
            select(
                Policy.section,
                Policy.title,
                Policy.text,
                Policy.fraud_classes,
                Policy.embedding.cosine_distance(vec).label("distance"),
            )
            .order_by(text("distance"))
            .limit(k)
        )
        with engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
    except Exception as e:  # noqa: BLE001 - DB unreachable -> graceful degrade
        return _unavailable(f"database unavailable ({type(e).__name__}): {e}")
    return {
        "available": True,
        "query_embedding_dim": len(vec),
        "n": len(rows),
        "policies": [
            {
                "section": r["section"],
                "title": r["title"],
                "text": r["text"],
                "fraud_classes": r["fraud_classes"],
                "cosine_distance": round(float(r["distance"]), 4),
            }
            for r in rows
        ],
    }
