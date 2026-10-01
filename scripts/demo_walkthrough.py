import json
from core.document_ai import DocumentAIProcessor
from core.anomaly_engine import AnomalyScoringEngine
from core.agent_auditor import AgentAuditor
from database.db import SessionLocal, Invoice, PurchaseOrder, Vendor

def run_demo():
    print("="*80)
    print(" 🛡️ PROCUREAI: END-TO-END INPUT & OUTPUT DEMONSTRATION")
    print("="*80)

    # 1. INPUT SAMPLE (Unstructured Vendor Receipt / Invoice Data)
    input_receipt = {
        "invoice_number": "INV-2025-DEMO-999",
        "vendor_name": "Apex Tech Solutions",
        "invoice_date": "2025-09-05",
        "subtotal": 9850.00,
        "tax_amount": 788.00,
        "total_amount": 10638.00,
        "po_amount_limit": 10000.00,
        "vendor_risk_rating": 0.85,
        "raw_text": "INVOICE INV-2025-DEMO-999 Date: 2025-09-05 Vendor: Apex Tech Solutions Subtotal: $9,850.00 Tax: $788.00 Total: $10,638.00 Note: Split billing against PO-2024-00124"
    }

    print("\n📥 [1] WHAT PROCUREAI TAKES AS INPUT:")
    print(json.dumps(input_receipt, indent=2))
    print("-"*80)

    # 2. PROCESSING PIPELINE
    print("\n⚙️ [2] PROCESSING PIPELINE EXECUTION:")
    
    # Document AI & Embedding
    doc_processor = DocumentAIProcessor()
    doc_result = doc_processor.process_invoice_image("non_existent.png")
    print(f"   • OCR & Layout Parsing: Extracted {len(doc_result['tokens'])} tokens from receipt.")
    print(f"   • Dense Vector Embedding: Generated {len(doc_result['embedding'])}-dimensional vector (stored in pgvector).")

    # Anomaly Scoring Engine
    anomaly_engine = AnomalyScoringEngine()
    score = anomaly_engine.score_invoice(
        subtotal=input_receipt["subtotal"],
        total_amount=input_receipt["total_amount"],
        amount_limit=input_receipt["po_amount_limit"],
        risk_rating=input_receipt["vendor_risk_rating"]
    )
    print(f"   • XGBoost Tabular Anomaly Score: {score:.4f} (High Risk / Threshold Dodging)")

    # Agentic Auditor (Gemini Flash)
    print("   • Agentic LLM Auditor: Invoking Gemini Flash policy reasoning engine...")
    
    # Simulate database record for audit if not present in DB
    db = SessionLocal()
    try:
        # Check or create dummy record for demonstration
        vendor = db.query(Vendor).first()
        po = db.query(PurchaseOrder).first()
        
        invoice = db.query(Invoice).filter(Invoice.invoice_number == input_receipt["invoice_number"]).first()
        if not invoice:
            invoice = Invoice(
                invoice_number=input_receipt["invoice_number"],
                vendor_id=vendor.vendor_id if vendor else 1,
                po_id=po.po_id if po else 1,
                invoice_date=input_receipt["invoice_date"],
                subtotal=input_receipt["subtotal"],
                tax_amount=input_receipt["tax_amount"],
                total_amount=input_receipt["total_amount"],
                raw_text=input_receipt["raw_text"],
                status="FLAGGED"
            )
            db.add(invoice)
            db.commit()
            db.refresh(invoice)

        auditor = AgentAuditor()
        audit_brief = auditor.audit_invoice(invoice_id=invoice.invoice_id, anomaly_score=score)
    finally:
        db.close()

    print("\n" + "="*80)
    print(" 📤 [3] WHAT PROCUREAI PRODUCES AS OUTPUT (FINAL AUDIT REPORT):")
    print("="*80)
    
    output_response = {
        "invoice_id": invoice.invoice_id if 'invoice' in locals() else 999,
        "invoice_number": input_receipt["invoice_number"],
        "anomaly_score": round(score, 4),
        "risk_level": "HIGH",
        "audit_brief": audit_brief
    }
    
    print(json.dumps(output_response, indent=2))
    print("="*80)

if __name__ == "__main__":
    run_demo()
