"""Prove the README claim: "96% anomaly detection accuracy".

Runs the repo's REAL synthesis (scripts/synthesize.py), the REAL feature
engineering (core/anomaly_engine.engineer_features) and the REAL training
configuration (AnomalyScoringEngine.train_dataframe: XGBoost, 100 trees,
depth 5, scale_pos_weight) on the full 250K-invoice dataset — no database
required. Reports accuracy / ROC AUC on a held-out 20% split.

Usage:
    .venv/bin/python proofs/prove_accuracy.py [--total 250000]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.synthesize import make_vendors, make_purchase_orders, make_invoices
from core.anomaly_engine import AnomalyScoringEngine

CLAIMED_ACCURACY = 0.96


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=250_000)
    args = ap.parse_args()

    t0 = time.time()
    vendors = make_vendors()
    pos = make_purchase_orders(vendors)
    invoices = make_invoices(vendors, pos, total=args.total)
    t_gen = time.time() - t0
    print(f"Generated {len(invoices):,} invoices in {t_gen:.1f}s")
    print("Fraud mix:", invoices["fraud_type"].value_counts().to_dict())

    df = invoices.merge(pos[["po_id", "amount_limit"]], on="po_id", how="left")
    df = df.merge(vendors[["vendor_id", "risk_rating"]], on="vendor_id", how="left")
    df["is_fraud"] = (df["status"] == "FLAGGED").astype(int)
    print(f"Fraud rate: {df['is_fraud'].mean():.3%}")

    engine = AnomalyScoringEngine()
    t1 = time.time()
    metrics = engine.train_dataframe(df)
    t_train = time.time() - t1
    print(f"Trained in {t_train:.1f}s on {metrics['n_train']:,} rows "
          f"(test: {metrics['n_test']:,} rows)")
    print(f"Accuracy: {metrics['accuracy']:.4f} | ROC AUC: {metrics['roc_auc']:.4f}")

    rep = metrics["report"]
    for label in ("0", "1"):
        r = rep[label]
        print(f"  class {label}: precision={r['precision']:.3f} "
              f"recall={r['recall']:.3f} f1={r['f1-score']:.3f}")

    if metrics["accuracy"] >= CLAIMED_ACCURACY:
        print(f"\nPASS: accuracy {metrics['accuracy']:.2%} >= claimed {CLAIMED_ACCURACY:.0%}")
    else:
        print(f"\nFAIL: accuracy {metrics['accuracy']:.2%} < claimed {CLAIMED_ACCURACY:.0%}")
        sys.exit(1)


if __name__ == "__main__":
    main()
