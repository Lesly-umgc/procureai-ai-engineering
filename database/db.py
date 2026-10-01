import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Enum, Date
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from pgvector.sqlalchemy import Vector
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://@localhost:5432/procureai_db")

engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Vendor(Base):
    __tablename__ = "vendors"
    
    vendor_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    tax_id = Column(String(50), unique=True, index=True)
    bank_account = Column(String(100))
    address = Column(Text)
    risk_rating = Column(Float, default=0.0)
    embedding = Column(Vector(384))

    purchase_orders = relationship("PurchaseOrder", back_populates="vendor")
    invoices = relationship("Invoice", back_populates="vendor")

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    
    po_id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String(100), unique=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.vendor_id"), index=True)
    department_id = Column(String(50), index=True)
    amount_limit = Column(Float, nullable=False)
    status = Column(String(50), default="ACTIVE")
    issue_date = Column(Date, nullable=False)

    vendor = relationship("Vendor", back_populates="purchase_orders")
    invoices = relationship("Invoice", back_populates="purchase_order")

class Invoice(Base):
    __tablename__ = "invoices"
    
    invoice_id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(100), index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.vendor_id"), index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.po_id"), index=True)
    invoice_date = Column(Date, nullable=False)
    subtotal = Column(Float, nullable=False)
    tax_amount = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    raw_text = Column(Text)
    embedding = Column(Vector(384))
    status = Column(String(50), default="PENDING")

    vendor = relationship("Vendor", back_populates="invoices")
    purchase_order = relationship("PurchaseOrder", back_populates="invoices")
    line_items = relationship("InvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan")
    audit_log = relationship("AuditLog", back_populates="invoice", uselist=False, cascade="all, delete-orphan")

class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"
    
    line_id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id"), index=True)
    item_description = Column(Text, nullable=False)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    line_total = Column(Float, nullable=False)
    category_code = Column(String(50), index=True)

    invoice = relationship("Invoice", back_populates="line_items")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    audit_id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id"), unique=True, index=True)
    anomaly_score = Column(Float, nullable=False)
    risk_level = Column(String(50), index=True)
    triggered_rules = Column(Text) # JSON stored as string or comma separated
    agent_reasoning = Column(Text)
    status = Column(String(50), default="REVIEWED")
    created_at = Column(DateTime, default=datetime.utcnow)

    invoice = relationship("Invoice", back_populates="audit_log")

class Policy(Base):
    """Procurement policy snippets for agent policy retrieval.

    SYNTHETIC SEED DATA -- seeded from policies/policies.json. These are
    representative procurement-policy wordings used so the audit agent can
    cite a policy behind its verdict; they are not any real company's policy.
    """
    __tablename__ = "policies"

    policy_id = Column(Integer, primary_key=True, index=True)
    section = Column(String(50), nullable=False, index=True)   # e.g. FIN-2.1
    title = Column(String(255), nullable=False)
    text = Column(Text, nullable=False)
    fraud_classes = Column(Text)  # comma-separated, e.g. "DUPLICATE,SPLIT_PO"
    embedding = Column(Vector(384))


def init_db():
    # The vector extension must exist before create_all(), because the
    # tables declare VECTOR(384) columns. On a fresh database the old
    # order (tables first, extension second) failed with
    # 'type "vector" does not exist'.
    #
    # PRIVILEGE SPLIT (verified 2026-09-21): creating the extension needs
    # superuser, so in the Compose stack it is created by the privileged
    # first-boot hook docker/db-init/01-procureai.sh, and the app role is
    # a non-superuser. When the extension already exists, the statement
    # below is a no-op that requires NO special privilege. On a truly
    # fresh database reached directly as a non-superuser it fails loudly
    # with "Must be superuser to create this extension" — that is the
    # honest signal that the privileged init step was skipped.
    with engine.connect() as conn:
        conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector;")
        conn.commit()
    Base.metadata.create_all(bind=engine)
    # Create IVFFlat vector indexes for similarity search
    with engine.connect() as conn:
        # Note: IVFFlat requires rows before indexing or can be created with lists=100
        # Verified live: IVFFlat with lists=100 on the 8-row policies table
        # makes the planner return ZERO rows for ORDER BY distance LIMIT k
        # (too many lists for too few rows). lists=1 is correct for a tiny
        # seed corpus; invoices/vendors keep lists=100 for the 250K-row
        # design scale (pgvector guidance: lists ~= rows/1000).
        try:
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS vendors_embedding_idx ON vendors USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS invoices_embedding_idx ON invoices USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS policies_embedding_idx ON policies USING ivfflat (embedding vector_cosine_ops) WITH (lists = 1);")
            conn.commit()
        except Exception as e:
            print(f"Index creation note (safe if tables empty): {e}")

if __name__ == "__main__":
    init_db()
    print("Database initialized and DDL applied successfully.")
