"""
Pure-Python synthetic data generation for ProcureAI (no database required).

Single source of truth for the synthetic data distribution, imported by:
  - scripts/generate_data.py  -> streams rows into PostgreSQL
  - proofs/prove_accuracy.py   -> validates the 96% accuracy claim without a DB
  - evals/                    -> shares the fraud taxonomy for the golden dataset

Distributions mirror the original generate_data.py. Two fraud types that were
previously label-only are implemented for real so the model's features are
honest signals:
  - DUPLICATE: a near-copy of a real NORMAL invoice (same vendor, amount
    within 0.3%, date within 3 days, mutated invoice number).
  - GHOST:    billed through one of the three high-risk vendors, so the
    vendor risk_rating feature is a genuine signal.
"""

import random
from datetime import datetime, timedelta

import pandas as pd

TOTAL_INVOICES = 250_000
BATCH_SIZE = 5_000
N_PURCHASE_ORDERS = 10_000

DEPARTMENTS = ["IT", "Engineering", "Marketing", "Operations", "Finance", "Legal"]
ITEM_CATEGORIES = ["HW", "SW", "CONSULTING", "OFFICE", "TRAVEL"]

VENDOR_NAMES = [
    "Apex Tech Solutions", "BlueRidge Logistics", "CyberGuard Systems", "Delta Industrial",
    "EcoClean Services", "Global Network Corp", "Hyperion Cloud", "Integra Soft",
    "Javelin Logistics", "Krypton Security", "Lumina Marketing", "Meridian Consulting",
    "Nexus Enterprises", "Omega Supplies", "Pioneer Data", "Quantum Dynamics",
    "Redwood Tech", "Summit Legal", "Titan Hardware", "Vertex Solutions",
]

# 1-based vendor_ids of the high-risk vendors used for GHOST fraud.
GHOST_VENDOR_IDS = (1, 2, 3)

FRAUD_TYPES = ["NORMAL", "DUPLICATE", "SPLIT_PO", "PRICE_DRIFT", "GHOST", "CALC_DISCREPANCY"]
FRAUD_WEIGHTS = [0.94, 0.012, 0.012, 0.012, 0.012, 0.012]

TAX_RATE = 0.08
APPROVAL_THRESHOLD = 10_000.0
BASE_DATE = datetime(2025, 9, 20)

VENDOR_COLUMNS = ["vendor_id", "name", "tax_id", "bank_account", "address", "risk_rating"]
PO_COLUMNS = ["po_id", "po_number", "vendor_id", "department_id", "amount_limit", "status", "issue_date"]
INVOICE_COLUMNS = ["invoice_number", "vendor_id", "po_id", "invoice_date", "subtotal",
                   "tax_amount", "total_amount", "raw_text", "fraud_type", "status", "duplicate_of"]


def random_embedding(dim: int = 384) -> str:
    """Placeholder embedding literal for bulk seeding (matches original loader).

    Real invoices processed through core/document_ai.py get genuine
    sentence-transformer embeddings; the bulk seed uses placeholders so a
    250K-row load does not require GPU/CPU embedding inference.
    """
    import numpy as np
    vec = np.random.randn(dim).astype("float32")
    vec /= np.linalg.norm(vec) + 1e-8
    return str(vec.tolist())


def make_vendors(seed: int = 42) -> pd.DataFrame:
    rng = random.Random(seed)
    rows = []
    for i, name in enumerate(VENDOR_NAMES, start=1):
        risk = round(rng.uniform(0.0, 0.2), 2)
        if i in GHOST_VENDOR_IDS:
            risk = 0.85
        rows.append((
            i,
            name,
            f"US-{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}",
            f"ACCT-{rng.randint(10000000, 99999999)}",
            f"{rng.randint(100, 9999)} Business Blvd, Suite {rng.randint(10, 500)}, City, State",
            risk,
        ))
    return pd.DataFrame(rows, columns=VENDOR_COLUMNS)


def make_purchase_orders(vendors: pd.DataFrame, n: int = N_PURCHASE_ORDERS,
                         seed: int = 42) -> pd.DataFrame:
    rng = random.Random(seed + 1)
    vendor_ids = vendors["vendor_id"].tolist()
    start = BASE_DATE - timedelta(days=730)
    rows = []
    for i in range(1, n + 1):
        rows.append((
            i,
            f"PO-2024-{i:05d}",
            rng.choice(vendor_ids),
            rng.choice(DEPARTMENTS),
            round(rng.uniform(5000.0, 50000.0), 2),
            "ACTIVE",
            (start + timedelta(days=rng.randint(1, 600))).date(),
        ))
    return pd.DataFrame(rows, columns=PO_COLUMNS)


def _base_amounts(rng: random.Random, fraud: str, po_limit: float):
    subtotal = round(rng.uniform(500.0, 9500.0), 2)
    if fraud == "SPLIT_PO":
        # Just under the $10K approval threshold (threshold dodging).
        subtotal = round(rng.uniform(9800.0, 9999.0), 2)
    elif fraud == "PRICE_DRIFT":
        # 30-60% above contracted PO terms.
        subtotal = round(po_limit * rng.uniform(1.3, 1.6), 2)
    tax = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + tax, 2)
    if fraud == "CALC_DISCREPANCY":
        # Total no longer matches subtotal + tax.
        total = round(total + rng.uniform(50.0, 500.0), 2)
    return subtotal, tax, total


def _row(invoice_number, vendor_id, po_id, invoice_date, subtotal, tax, total,
         fraud, duplicate_of=None):
    raw_text = (f"INVOICE {invoice_number} Date: {invoice_date.strftime('%Y-%m-%d')} "
                f"Subtotal: ${subtotal} Tax: ${tax} Total: ${total}")
    status = "FLAGGED" if fraud != "NORMAL" else "APPROVED"
    return (invoice_number, vendor_id, po_id, invoice_date, subtotal, tax,
            total, raw_text, fraud, status, duplicate_of)


def make_invoices(vendors: pd.DataFrame, pos: pd.DataFrame,
                  total: int = TOTAL_INVOICES, seed: int = 42) -> pd.DataFrame:
    """Generate `total` invoices. Pass 1 creates everything except duplicates;
    pass 2 creates DUPLICATE rows as near-copies of real NORMAL invoices."""
    rng = random.Random(seed + 2)
    po_ids = pos["po_id"].tolist()
    po_by_id = {r.po_id: r for r in pos.itertuples()}
    ghost_po_ids = [p for p in po_ids if po_by_id[p].vendor_id in GHOST_VENDOR_IDS]

    rows = []
    normal_idx = []
    dup_deferred = 0
    seq = 0

    for _ in range(total):
        fraud = rng.choices(FRAUD_TYPES, weights=FRAUD_WEIGHTS, k=1)[0]
        if fraud == "DUPLICATE":
            dup_deferred += 1
            continue
        seq += 1
        po_id = rng.choice(ghost_po_ids) if fraud == "GHOST" else rng.choice(po_ids)
        po = po_by_id[po_id]
        subtotal, tax, total_amt = _base_amounts(rng, fraud, po.amount_limit)
        inv_date = BASE_DATE - timedelta(days=rng.randint(1, 365))
        rows.append(_row(f"INV-2025-{seq:07d}", po.vendor_id, po_id, inv_date,
                         subtotal, tax, total_amt, fraud))
        if fraud == "NORMAL":
            normal_idx.append(len(rows) - 1)

    for _ in range(dup_deferred):
        seq += 1
        src = rows[rng.choice(normal_idx)]
        (src_num, vendor_id, po_id, src_date, subtotal, _tax, _total,
         _raw, _fraud, _status, _dup) = src
        jitter = rng.uniform(0.997, 1.003)
        subtotal2 = round(subtotal * jitter, 2)
        tax2 = round(subtotal2 * TAX_RATE, 2)
        total2 = round(subtotal2 + tax2, 2)
        inv_date = src_date + timedelta(days=rng.randint(-3, 3))
        rows.append(_row(f"INV-2025-{seq:07d}", vendor_id, po_id, inv_date,
                         subtotal2, tax2, total2, "DUPLICATE", duplicate_of=src_num))

    return pd.DataFrame(rows, columns=INVOICE_COLUMNS)


def iter_invoice_batches(vendors: pd.DataFrame, pos: pd.DataFrame,
                         total: int = TOTAL_INVOICES, batch_size: int = BATCH_SIZE,
                         seed: int = 42):
    """Yield invoice DataFrames in batches (for streaming DB loads)."""
    df = make_invoices(vendors, pos, total=total, seed=seed)
    for start in range(0, len(df), batch_size):
        yield df.iloc[start:start + batch_size]
