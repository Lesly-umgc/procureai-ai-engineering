"""Deterministic tool tests: verify_arithmetic, find_duplicates, check_po,
assess_vendor. No LLM, no DB, no model — pure functions on dicts."""
from __future__ import annotations

import copy

from core.agent.tools import (
    assess_vendor,
    check_po,
    find_duplicates,
    verify_arithmetic,
)


# ---------------------------------------------------------------------------
# verify_arithmetic
# ---------------------------------------------------------------------------

class TestVerifyArithmetic:
    def test_clean_invoice_ok(self, clean_invoice):
        out = verify_arithmetic(clean_invoice)
        assert out["ok"] is True
        assert out["findings"] == []
        assert out["total_diff"] == 0.0
        assert out["lines_ok"] is True

    def test_overstated_total_reports_exact_diff(self, clean_invoice):
        inv = copy.deepcopy(clean_invoice)
        inv["total_amount"] = 1130.00  # overstated by 50.00
        out = verify_arithmetic(inv)
        assert out["ok"] is False
        assert out["total_diff"] == 50.0
        assert out["expected_total"] == 1080.0
        assert any("overstated by $50.00" in f for f in out["findings"])

    def test_line_items_mismatch(self, clean_invoice):
        inv = copy.deepcopy(clean_invoice)
        inv["line_items"][0]["line_total"] = 900.00  # lines sum to 900, subtotal 1000
        out = verify_arithmetic(inv)
        assert out["ok"] is False
        assert out["lines_ok"] is False
        assert out["lines_total"] == 900.0
        assert any("discrepancy of $100.00" in f for f in out["findings"])

    def test_wrong_tax_rate(self, clean_invoice):
        inv = copy.deepcopy(clean_invoice)
        # subtotal 1000 @ 8% -> expected tax 80; charge 100 and fix the total
        # so the ONLY failure is the tax line.
        inv["tax_amount"] = 100.00
        inv["total_amount"] = 1100.00
        out = verify_arithmetic(inv)
        assert out["ok"] is False
        assert any("tax mismatch" in f for f in out["findings"])
        assert not any("total mismatch" in f for f in out["findings"])

    def test_no_line_items_is_not_a_failure(self, clean_invoice):
        inv = copy.deepcopy(clean_invoice)
        inv["line_items"] = []
        out = verify_arithmetic(inv)
        assert out["lines_ok"] is True
        assert out["ok"] is True


# ---------------------------------------------------------------------------
# find_duplicates
# ---------------------------------------------------------------------------

def _hist_row(inv_id, vendor="V-T1", po="PO-T1", amount=1080.0, date="2026-01-01"):
    return {
        "invoice_id": inv_id,
        "vendor_id": vendor,
        "po_id": po,
        "total_amount": amount,
        "invoice_date": date,
    }


class TestFindDuplicates:
    def test_exact_duplicate_found(self, clean_invoice):
        hist = [_hist_row("INV-OLD", amount=1080.0, date="2026-01-01")]
        out = find_duplicates(clean_invoice, hist)
        assert out["duplicate_found"] is True
        assert out["matches"][0]["invoice_id"] == "INV-OLD"
        assert out["matches"][0]["amount_diff_pct"] == 0.0

    def test_amount_within_2pct_matches(self, clean_invoice):
        # 1096.20 is 1.5% above 1080 -> inside the 2% window
        hist = [_hist_row("INV-OLD", amount=1096.20, date="2026-01-10")]
        out = find_duplicates(clean_invoice, hist)
        assert out["duplicate_found"] is True
        assert out["matches"][0]["amount_diff_pct"] == 1.5

    def test_amount_outside_window_no_match(self, clean_invoice):
        hist = [_hist_row("INV-OLD", amount=1200.0, date="2026-01-10")]
        assert find_duplicates(clean_invoice, hist)["duplicate_found"] is False

    def test_date_outside_45_days_no_match(self, clean_invoice):
        hist = [_hist_row("INV-OLD", amount=1080.0, date="2025-10-01")]
        assert find_duplicates(clean_invoice, hist)["duplicate_found"] is False

    def test_different_vendor_or_po_no_match(self, clean_invoice):
        hist = [
            _hist_row("INV-A", vendor="V-OTHER"),
            _hist_row("INV-B", po="PO-OTHER"),
        ]
        assert find_duplicates(clean_invoice, hist)["duplicate_found"] is False

    def test_self_is_skipped(self, clean_invoice):
        hist = [_hist_row("INV-T1", amount=1080.0, date="2026-01-15")]
        assert find_duplicates(clean_invoice, hist)["duplicate_found"] is False

    def test_empty_history(self, clean_invoice):
        out = find_duplicates(clean_invoice, [])
        assert out == {"duplicate_found": False, "matches": []}


# ---------------------------------------------------------------------------
# check_po
# ---------------------------------------------------------------------------

class TestCheckPo:
    def test_within_limit(self, clean_invoice, clean_po):
        out = check_po(clean_invoice, clean_po)
        assert out["over_limit"] is False
        # overage is signed: negative means under the limit, not clamped
        assert out["overage"] == -3920.0
        assert out["finding"] is None
        assert out["billed"] == 1080.0
        assert out["po_limit"] == 5000.0

    def test_over_limit_reports_exact_overage(self, clean_invoice):
        inv = copy.deepcopy(clean_invoice)
        inv["total_amount"] = 12000.00
        out = check_po(inv, {"po_id": "PO-T1", "amount_limit": 10000.0})
        assert out["over_limit"] is True
        assert out["overage"] == 2000.0
        assert out["over_pct"] == 20.0
        assert "exceeds PO limit" in out["finding"]

    def test_price_drift_above_10pct(self, clean_invoice, clean_po):
        inv = copy.deepcopy(clean_invoice)
        inv["line_items"][0]["unit_price"] = 115.0  # 15% above contract
        inv["line_items"][0]["line_total"] = 1150.0
        inv["subtotal"] = 1150.0
        inv["tax_amount"] = 92.0
        inv["total_amount"] = 1242.0
        out = check_po(inv, clean_po)
        assert len(out["price_drift_lines"]) == 1
        assert "15.0% above PO contracted rate" in out["price_drift_lines"][0]

    def test_no_drift_within_tolerance(self, clean_invoice, clean_po):
        inv = copy.deepcopy(clean_invoice)
        inv["line_items"][0]["unit_price"] = 105.0  # 5% above: tolerated
        out = check_po(inv, clean_po)
        assert out["price_drift_lines"] == []

    def test_near_10k_threshold(self, clean_invoice, clean_po):
        inv = copy.deepcopy(clean_invoice)
        inv["total_amount"] = 9750.00  # 250 below the $10k approval threshold
        out = check_po(inv, clean_po)
        assert out["near_10k_threshold"] is True

    def test_not_near_threshold(self, clean_invoice, clean_po):
        out = check_po(clean_invoice, clean_po)  # total 1080
        assert out["near_10k_threshold"] is False


# ---------------------------------------------------------------------------
# assess_vendor
# ---------------------------------------------------------------------------

class TestAssessVendor:
    def test_clean_vendor_no_ghost_signals(self, clean_vendor, vendor_history):
        out = assess_vendor(clean_vendor, vendor_history)
        assert out["ghost_suspect"] is False
        assert out["ghost_signals"] == []
        assert out["on_file"] is True
        assert out["tax_id_verified"] is True
        assert out["prior_invoices"] == 3
        assert out["prior_flagged"] == 0

    def test_ghost_vendor_all_signals(self):
        vendor = {
            "vendor_id": "V-GHOST",
            "vendor_name": "Ghost LLC",
            "risk_rating": 0.85,
            "on_file": False,
            "tax_id": "UNVERIFIED",
        }
        out = assess_vendor(vendor, [])
        assert out["ghost_suspect"] is True
        assert out["tax_id_verified"] is False
        joined = " | ".join(out["ghost_signals"])
        assert "NOT in approved vendor master" in joined
        assert "tax ID UNVERIFIED" in joined
        assert "0.85 >= 0.70" in joined
        assert "thin history" in joined

    def test_thin_history_alone_is_a_signal(self, clean_vendor):
        out = assess_vendor(clean_vendor, [])  # clean but brand-new
        assert out["ghost_suspect"] is True
        assert any("thin history" in s for s in out["ghost_signals"])
        # ...but it is not a hard ghost verdict: still on file, tax verified
        assert out["on_file"] is True
        assert out["tax_id_verified"] is True

    def test_flagged_history_counted(self, clean_vendor, vendor_history):
        vendor_history[0]["status"] = "FLAGGED"
        out = assess_vendor(clean_vendor, vendor_history)
        assert out["prior_flagged"] == 1
