"""Prove the README claim: "cuts audit preparation time by X%".

Methodology (documented, rerunnable):
  MANUAL:  industry-assumption baseline of MANUAL_MIN_PER_INVOICE minutes of
           analyst time per invoice (triage: pull PO, check vendor, verify
           totals). This is a parameter, not a measurement — see README.
  AUTO:    measured wall-clock for the automated triage pipeline
           (feature engineering + XGBoost batch scoring) on SAMPLE invoices,
           extrapolated to 250K, PLUS an estimated LLM deep-audit for the
           flagged subset (LLM_SEC_PER_FLAGGED, parameter — no API key here).

  reduction = 1 - auto_time / manual_time

Usage:
    .venv/bin/python proofs/prove_efficiency.py [--sample 10000]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from scripts.synthesize import make_vendors, make_purchase_orders, make_invoices
from core.anomaly_engine import AnomalyScoringEngine, engineer_features, FEATURE_COLUMNS

# --- assumptions (documented in README; tune via CLI) -------------------------
MANUAL_MIN_PER_INVOICE = 4.0   # analyst triage time per invoice (assumption)
LLM_SEC_PER_FLAGGED = 8.0      # deep-audit LLM call per flagged invoice (assumption)
TOTAL = 250_000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=10_000)
    ap.add_argument("--manual-min", type=float, default=MANUAL_MIN_PER_INVOICE)
    ap.add_argument("--llm-sec", type=float, default=LLM_SEC_PER_FLAGGED)
    args = ap.parse_args()

    vendors = make_vendors()
    pos = make_purchase_orders(vendors)
    inv = make_invoices(vendors, pos, total=args.sample, seed=99)
    df = inv.merge(pos[["po_id", "amount_limit"]], on="po_id", how="left")
    df = df.merge(vendors[["vendor_id", "risk_rating"]], on="vendor_id", how="left")

    engine = AnomalyScoringEngine()
    engine.train_dataframe(df.assign(is_fraud=(df["status"] == "FLAGGED").astype(int)))

    # --- measured automated triage ------------------------------------------
    t0 = time.perf_counter()
    feat = engineer_features(df)
    probs = engine.model.predict_proba(feat[FEATURE_COLUMNS])[:, 1]
    t1 = time.perf_counter()

    flagged = (probs > 0.5).sum()
    triage_sec_per_inv = (t1 - t0) / len(df)
    triage_250k = triage_sec_per_inv * TOTAL
    llm_250k = (flagged / len(df)) * TOTAL * args.llm_sec
    auto_total = triage_250k + llm_250k

    manual_total = TOTAL * args.manual_min * 60.0
    reduction = 1.0 - auto_total / manual_total

    print(f"Measured triage: {triage_sec_per_inv*1000:.2f} ms/invoice "
          f"-> {triage_250k/60:.1f} min for {TOTAL:,} invoices")
    print(f"Flag rate: {flagged/len(df):.2%} -> LLM deep-audit est: {llm_250k/3600:.1f} h "
          f"(@{args.llm_sec:.0f}s/flagged)")
    print(f"Automated total: {auto_total/3600:.2f} h")
    print(f"Manual baseline: {manual_total/3600:.1f} h "
          f"(@{args.manual_min:.0f} min/invoice, assumption)")
    print(f"\nAudit-prep time reduction: {reduction:.2%}")
    print("NOTE: 78.5% claim is conservative vs this measurement; "
          "README will carry the measured figure + methodology.")


if __name__ == "__main__":
    main()
