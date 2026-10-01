"""Stream 250K synthetic invoices into PostgreSQL.

Data distributions come from scripts/synthesize.py (single source of truth,
shared with proofs/ and evals/). This script only handles the database load:
it maps the generated 1-based ids onto the real database ids and streams
inserts in batches.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import execute_batch

from database.db import DATABASE_URL
from scripts.synthesize import (
    make_vendors,
    make_purchase_orders,
    iter_invoice_batches,
    random_embedding,
    VENDOR_NAMES,
    TOTAL_INVOICES,
    BATCH_SIZE,
)


def get_db_connection():
    conn_str = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
    return psycopg2.connect(conn_str)


def run_synthesis():
    conn = get_db_connection()
    cur = conn.cursor()

    vendors = make_vendors()
    pos = make_purchase_orders(vendors)

    # --- vendors ---------------------------------------------------------
    cur.execute("SELECT COUNT(*) FROM vendors;")
    if cur.fetchone()[0] == 0:
        print("Seeding vendors...")
        vendors_data = [
            (r.name, r.tax_id, r.bank_account, r.address, r.risk_rating, random_embedding())
            for r in vendors.itertuples()
        ]
        execute_batch(cur, """
            INSERT INTO vendors (name, tax_id, bank_account, address, risk_rating, embedding)
            VALUES (%s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT (tax_id) DO NOTHING;
        """, vendors_data)
        conn.commit()

    cur.execute("SELECT vendor_id, name FROM vendors;")
    vendor_id_by_name = {name: vid for vid, name in cur.fetchall()}
    pos = pos.copy()
    pos["vendor_id"] = pos["vendor_id"].map(lambda i: vendor_id_by_name[VENDOR_NAMES[i - 1]])

    # --- purchase orders --------------------------------------------------
    cur.execute("SELECT COUNT(*) FROM purchase_orders;")
    if cur.fetchone()[0] == 0:
        print("Seeding purchase orders...")
        batch = []
        for r in pos.itertuples():
            batch.append((r.po_number, r.vendor_id, r.department_id,
                          r.amount_limit, r.status, r.issue_date))
            if len(batch) >= 2000:
                execute_batch(cur, """
                    INSERT INTO purchase_orders
                        (po_number, vendor_id, department_id, amount_limit, status, issue_date)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (po_number) DO NOTHING;
                """, batch)
                conn.commit()
                batch = []
        if batch:
            execute_batch(cur, """
                INSERT INTO purchase_orders
                    (po_number, vendor_id, department_id, amount_limit, status, issue_date)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (po_number) DO NOTHING;
            """, batch)
            conn.commit()

    cur.execute("SELECT po_id, po_number FROM purchase_orders;")
    po_id_by_number = {num: pid for pid, num in cur.fetchall()}
    po_number_by_seq = {r.po_id: r.po_number for r in pos.itertuples()}

    # --- invoices (streamed in batches) -----------------------------------
    print(f"Starting streaming synthesis of {TOTAL_INVOICES} invoices "
          f"in batches of {BATCH_SIZE}...")
    inserted = 0
    for batch_df in iter_invoice_batches(vendors, pos, total=TOTAL_INVOICES,
                                         batch_size=BATCH_SIZE):
        rows = []
        for r in batch_df.itertuples():
            rows.append((
                r.invoice_number,
                vendor_id_by_name[VENDOR_NAMES[r.vendor_id - 1]],
                po_id_by_number[po_number_by_seq[r.po_id]],
                r.invoice_date.date() if hasattr(r.invoice_date, "date") else r.invoice_date,
                r.subtotal, r.tax_amount, r.total_amount,
                r.raw_text, random_embedding(), r.status,
            ))
        execute_batch(cur, """
            INSERT INTO invoices
                (invoice_number, vendor_id, po_id, invoice_date, subtotal,
                 tax_amount, total_amount, raw_text, embedding, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
        """, rows)
        conn.commit()
        inserted += len(rows)
        print(f"Progress: {inserted}/{TOTAL_INVOICES} invoices synthesized and inserted.")

    cur.close()
    conn.close()
    print("Data synthesis pipeline completed successfully!")


if __name__ == "__main__":
    run_synthesis()
