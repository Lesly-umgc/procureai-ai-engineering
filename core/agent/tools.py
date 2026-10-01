"""Deterministic tools for the ProcureAI audit agent.

Each tool is a pure function (no LLM) encapsulating real repo logic:
XGBoost scoring, arithmetic verification, duplicate detection, PO matching,
vendor risk. The agent (agent.py) calls these in a ReAct loop; the LLM
reasons, the tools compute. That is what makes it an agent rather than
a single prompt.

All tools work on plain dicts and need no database — data comes from
scripts/synthesize.py (same distribution as the proofs) or the caller.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

from core.anomaly_engine import AnomalyScoringEngine, FEATURE_COLUMNS
from core.agent.retrieval import find_similar_invoices, retrieve_policy

# ---------------------------------------------------------------------------
# Shared engine (loaded once)
# ---------------------------------------------------------------------------
_engine: AnomalyScoringEngine | None = None


def _get_engine() -> AnomalyScoringEngine:
    global _engine
    if _engine is None:
        _engine = AnomalyScoringEngine()
    return _engine


# ---------------------------------------------------------------------------
# Tool: XGBoost anomaly score
# ---------------------------------------------------------------------------
def score_invoice_xgb(invoice: Dict[str, Any]) -> Dict[str, Any]:
    """Score one invoice with the trained XGBoost model.

    invoice needs: subtotal, total_amount, amount_limit, risk_rating.
    Returns anomaly_score (0-1) and a risk_level string.
    """
    eng = _get_engine()
    if eng.model is None:
        return {"error": "model not trained — call train first", "anomaly_score": 0.0}
    score = eng.score_invoice(
        subtotal=float(invoice.get("subtotal", 0)),
        total_amount=float(invoice.get("total_amount", 0)),
        amount_limit=float(invoice.get("amount_limit", 0)),
        risk_rating=float(invoice.get("risk_rating", 0)),
    )
    score = float(score)
    risk_level = "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"
    return {
        "anomaly_score": round(score, 4),
        "risk_level": risk_level,
    }


# ---------------------------------------------------------------------------
# Tool: arithmetic verification (deterministic)
# ---------------------------------------------------------------------------
def verify_arithmetic(invoice: Dict[str, Any], tax_rate: float = 0.08) -> Dict[str, Any]:
    """Check subtotal + tax == total and line items sum to subtotal.

    Returns ok=False with the exact discrepancy when the math doesn't add up.
    This catches CALC_DISCREPANCY with certainty — no ML needed.
    """
    subtotal = float(invoice.get("subtotal", 0))
    tax = float(invoice.get("tax_amount", 0))
    total = float(invoice.get("total_amount", 0))
    expected_total = round(subtotal + tax, 2)
    total_ok = abs(expected_total - total) < 0.01

    expected_tax = round(subtotal * tax_rate, 2)
    tax_ok = abs(expected_tax - tax) < 0.01

    line_items = invoice.get("line_items", []) or []
    lines_total = round(sum(float(l.get("line_total", 0)) for l in line_items), 2)
    lines_ok = True
    if line_items:
        lines_ok = abs(lines_total - subtotal) < 0.01

    findings = []
    if not lines_ok:
        findings.append(
            f"line items sum to ${lines_total:,.2f} but stated subtotal is "
            f"${subtotal:,.2f} (discrepancy of ${subtotal - lines_total:,.2f})"
        )
    if not total_ok:
        findings.append(
            f"total mismatch: subtotal ${subtotal:,.2f} + tax ${tax:,.2f} = "
            f"${expected_total:,.2f} expected, but billed ${total:,.2f} "
            f"(overstated by ${total - expected_total:,.2f})"
        )
    if not tax_ok:
        findings.append(
            f"tax mismatch: {tax_rate:.0%} of ${subtotal:,.2f} = ${expected_tax:,.2f} "
            f"expected, but ${tax:,.2f} charged"
        )
    return {
        "ok": total_ok and tax_ok and lines_ok,
        "expected_total": expected_total,
        "actual_total": total,
        "total_diff": round(total - expected_total, 2),
        "lines_total": lines_total,
        "lines_ok": lines_ok,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Tool: duplicate detection (deterministic)
# ---------------------------------------------------------------------------
def find_duplicates(invoice: Dict[str, Any], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Find near-duplicate invoices in history: same vendor + same PO + amount
    within 2% + date within 45 days. Returns matches with evidence."""
    vendor = invoice.get("vendor_id")
    po = invoice.get("po_id")
    amount = float(invoice.get("total_amount", 0))
    date = pd.to_datetime(invoice.get("invoice_date"))
    inv_id = invoice.get("invoice_id")

    matches = []
    for h in history:
        if h.get("invoice_id") == inv_id:
            continue
        if h.get("vendor_id") != vendor or h.get("po_id") != po:
            continue
        h_amount = float(h.get("total_amount", 0))
        if abs(h_amount - amount) / max(amount, 1) > 0.02:
            continue
        h_date = pd.to_datetime(h.get("invoice_date"))
        if abs((date - h_date).days) > 45:
            continue
        matches.append({
            "invoice_id": h.get("invoice_id"),
            "total_amount": h_amount,
            "invoice_date": str(h.get("invoice_date")),
            "amount_diff_pct": round(abs(h_amount - amount) / max(amount, 1) * 100, 2),
        })
    return {"duplicate_found": len(matches) > 0, "matches": matches}


# ---------------------------------------------------------------------------
# Tool: PO matching (deterministic)
# ---------------------------------------------------------------------------
def check_po(invoice: Dict[str, Any], po: Dict[str, Any]) -> Dict[str, Any]:
    """Compare billed total against the PO amount limit and each line's unit
    price against its PO contracted rate.

    Returns over_limit flag and overage — catches SPLIT_PO — plus per-line
    price-drift findings — catches PRICE_DRIFT with certainty, no ML needed.
    """
    total = float(invoice.get("total_amount", 0))
    limit = float(po.get("amount_limit", 0))
    over = total - limit
    over_pct = (over / limit * 100) if limit else 0.0
    near_threshold = abs(total - 10_000.0) < 500  # just under approval threshold

    drift_lines = []
    for line in invoice.get("line_items", []) or []:
        unit = line.get("unit_price")
        contracted = line.get("po_unit_price")
        if unit is None or contracted is None:
            continue
        unit, contracted = float(unit), float(contracted)
        if contracted > 0 and unit > contracted * 1.10:  # >10% above contract
            drift_pct = round((unit - contracted) / contracted * 100, 1)
            drift_lines.append(
                f"{line.get('description', 'line item')}: unit price "
                f"${unit:,.2f} is {drift_pct}% above PO contracted rate "
                f"${contracted:,.2f}"
            )
    return {
        "po_id": po.get("po_id"),
        "billed": round(total, 2),
        "po_limit": round(limit, 2),
        "over_limit": over > 0,
        "overage": round(over, 2),
        "over_pct": round(over_pct, 2),
        "near_10k_threshold": near_threshold,
        "price_drift_lines": drift_lines,
        "finding": (
            f"billed ${total:,.2f} exceeds PO limit ${limit:,.2f} by "
            f"${over:,.2f} ({over_pct:.1f}%)" if over > 0 else None
        ),
    }


# ---------------------------------------------------------------------------
# Tool: vendor risk (deterministic)
# ---------------------------------------------------------------------------
def assess_vendor(vendor: Dict[str, Any], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize vendor risk: master-file standing, tax-ID verification,
    rating, invoice volume, past flags.

    A vendor NOT on the approved vendor master, or with an unverified tax ID,
    is a hard ghost-vendor signal — certain fraud, not mere suspicion.
    """
    vendor_id = vendor.get("vendor_id")
    rating = float(vendor.get("risk_rating", 0))
    past = [h for h in history if h.get("vendor_id") == vendor_id]
    flagged = sum(1 for h in past if h.get("status") == "FLAGGED")
    on_file = vendor.get("on_file", True)
    tax_id = vendor.get("tax_id", "")
    tax_verified = bool(tax_id) and str(tax_id).upper() != "UNVERIFIED"
    ghost_signals = []
    if not on_file:
        ghost_signals.append("vendor NOT in approved vendor master")
    if not tax_verified:
        ghost_signals.append("tax ID UNVERIFIED")
    if rating >= 0.7:
        ghost_signals.append(f"risk rating {rating:.2f} >= 0.70 (high-risk vendor)")
    if len(past) <= 2:
        ghost_signals.append(f"only {len(past)} prior invoices (thin history)")
    return {
        "vendor_id": vendor_id,
        "vendor_name": vendor.get("vendor_name"),
        "on_file": on_file,
        "tax_id_verified": tax_verified,
        "risk_rating": rating,
        "prior_invoices": len(past),
        "prior_flagged": flagged,
        "ghost_suspect": len(ghost_signals) > 0,
        "ghost_signals": ghost_signals,
    }


# ---------------------------------------------------------------------------
# Tool registry (name -> function + description for the agent prompt)
# ---------------------------------------------------------------------------
TOOLS = {
    "score_invoice_xgb": (
        score_invoice_xgb,
        "Score the invoice with the XGBoost anomaly model. Input: invoice dict. "
        "Returns anomaly_score 0-1 and risk_level.",
    ),
    "verify_arithmetic": (
        verify_arithmetic,
        "Verify subtotal + tax == total exactly. Input: invoice dict. "
        "Returns ok flag and exact discrepancies. Use first — it is certain.",
    ),
    "find_duplicates": (
        find_duplicates,
        "Search history for near-duplicate invoices (same vendor+PO, amount within "
        "2%, date within 45 days). Input: invoice dict, history list.",
    ),
    "check_po": (
        check_po,
        "Compare billed total to the PO amount limit AND each line's unit price "
        "to its PO contracted rate. Input: invoice dict, po dict. Returns "
        "over_limit flag, overage, near_10k_threshold, and price_drift_lines.",
    ),
    "assess_vendor": (
        assess_vendor,
        "Assess vendor risk and ghost-vendor signals. Input: vendor dict, history list.",
    ),
    "find_similar_invoices": (
        find_similar_invoices,
        "Find the k most similar historical invoices via pgvector cosine search "
        "(needs Postgres+pgvector; returns available=false when the DB is "
        "unreachable). Input: invoice dict. Returns amounts, dates, seeded "
        "statuses (FLAGGED/APPROVED) and cosine distances for comparison.",
    ),
    "retrieve_policy": (
        retrieve_policy,
        "Retrieve the most relevant policy snippets via pgvector cosine search "
        "over the policies table (synthetic seed corpus; returns available=false "
        "when the DB is unreachable). Input: invoice dict. Returns section "
        "code, title, text, and covered fraud classes for citation.",
    ),
}


def tool_descriptions() -> str:
    lines = []
    for name, (_, desc) in TOOLS.items():
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)
