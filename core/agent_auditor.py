import os
import json
import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
from database.db import SessionLocal, Invoice, AuditLog

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

class AgentAuditor:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or GEMINI_API_KEY

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def call_gemini_api(self, prompt: str) -> str:
        if not self.api_key:
            return json.dumps({
                "summary": "Mock audit verification: Invoice verified against procurement policy rules.",
                "cited_policy_rules": ["SEC-4.2: Purchase Order Threshold Limit"],
                "risk_verdict": "FLAGGED",
                "reasoning": "Simulated agent reasoning due to missing API key."
            })

        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }
        url = f"{GEMINI_API_URL}?key={self.api_key}"
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code != 200:
                print(f"Gemini API error {response.status_code}: {response.text}")
                # Fallback to gemini-flash-latest
                fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={self.api_key}"
                response = requests.post(fallback_url, headers=headers, json=payload, timeout=30)

            response.raise_for_status()
            res_json = response.json()
            return res_json["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            print(f"Gemini API call failed: {e}")
            raise

    def audit_invoice(self, invoice_id: int, anomaly_score: float) -> dict:
        db = SessionLocal()
        try:
            invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
            if not invoice:
                return {"error": "Invoice not found"}

            po = invoice.purchase_order
            vendor = invoice.vendor

            prompt = f"""
            You are ProcureAI Enterprise Compliance Auditor. Analyze the following high-risk invoice and produce a strict JSON audit brief.
            
            Invoice Details:
            - Invoice Number: {invoice.invoice_number}
            - Date: {invoice.invoice_date}
            - Subtotal: ${invoice.subtotal}
            - Total Amount: ${invoice.total_amount}
            - Raw OCR Text: {invoice.raw_text}
            
            Purchase Order Terms:
            - PO Number: {po.po_number if po else 'N/A'}
            - Amount Limit: ${po.amount_limit if po else 'N/A'}
            - Department: {po.department_id if po else 'N/A'}
            
            Vendor Details:
            - Vendor Name: {vendor.name if vendor else 'N/A'}
            - Tax ID: {vendor.tax_id if vendor else 'N/A'}
            - Risk Rating: {vendor.risk_rating if vendor else 'N/A'}
            
            Model Anomaly Score: {anomaly_score:.2f}
            
            Provide a JSON response with keys:
            - "summary": string summary of findings
            - "cited_policy_rules": list of policy rules violated or verified
            - "risk_verdict": "APPROVE" | "FLAG" | "REJECT"
            - "reasoning": detailed multi-step compliance reasoning
            """

            agent_response_str = self.call_gemini_api(prompt)
            brief = json.loads(agent_response_str)

            risk_level = "HIGH" if anomaly_score > 0.80 else ("MEDIUM" if anomaly_score > 0.50 else "LOW")
            
            existing_log = db.query(AuditLog).filter(AuditLog.invoice_id == invoice_id).first()
            if existing_log:
                existing_log.anomaly_score = anomaly_score
                existing_log.risk_level = risk_level
                existing_log.triggered_rules = json.dumps(brief.get("cited_policy_rules", []))
                existing_log.agent_reasoning = json.dumps(brief)
                existing_log.status = brief.get("risk_verdict", "REVIEWED")
            else:
                audit_log = AuditLog(
                    invoice_id=invoice_id,
                    anomaly_score=anomaly_score,
                    risk_level=risk_level,
                    triggered_rules=json.dumps(brief.get("cited_policy_rules", [])),
                    agent_reasoning=json.dumps(brief),
                    status=brief.get("risk_verdict", "REVIEWED")
                )
                db.add(audit_log)
            
            db.commit()
            return brief

        except Exception as e:
            db.rollback()
            print(f"Error auditing invoice {invoice_id}: {e}")
            return {"error": str(e)}
        finally:
            db.close()

if __name__ == "__main__":
    auditor = AgentAuditor()
    res = auditor.audit_invoice(invoice_id=1, anomaly_score=0.92)
    print("Live Gemini Agent Auditor Test Result:", res)
