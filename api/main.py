from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv()

from database.db import SessionLocal, Invoice, PurchaseOrder, Vendor, AuditLog
from core.anomaly_engine import AnomalyScoringEngine
from core.agent_auditor import AgentAuditor

app = FastAPI(title="ProcureAI Enterprise API", version="1.0.0")

anomaly_engine = AnomalyScoringEngine()
# Try training model on startup if data exists
try:
    anomaly_engine.train_model()
except Exception as e:
    print(f"Model init training note: {e}")

agent_auditor = AgentAuditor()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class AuditRequest(BaseModel):
    invoice_id: int

@app.get("/health")
def health_check():
    return {"status": "healthy", "system": "ProcureAI Enterprise Engine"}

@app.get("/metrics")
def get_metrics(db: Session = Depends(get_db)):
    total_invoices = db.query(Invoice).count()
    flagged_invoices = db.query(Invoice).filter(Invoice.status == "FLAGGED").count()
    total_audits = db.query(AuditLog).count()
    
    # Calculate sum of flagged invoice amounts
    flagged_amount_res = db.query(Invoice).filter(Invoice.status == "FLAGGED").all()
    flagged_fraud_amount = sum([inv.total_amount for inv in flagged_amount_res])

    return {
        "total_invoices": total_invoices,
        "flagged_invoices": flagged_invoices,
        "total_audits": total_audits,
        "flagged_fraud_amount": round(flagged_fraud_amount, 2),
        # Measured via proofs/prove_efficiency.py (99.87% reduction vs a
        # 4-min/invoice manual baseline). Re-run the proof if the pipeline changes.
        "audit_prep_time_reduction_pct": 99.87
    }

@app.get("/invoices")
def list_invoices(skip: int = 0, limit: int = 50, status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Invoice)
    if status:
        query = query.filter(Invoice.status == status.upper())
    invoices = query.offset(skip).limit(limit).all()
    
    results = []
    for inv in invoices:
        results.append({
            "invoice_id": inv.invoice_id,
            "invoice_number": inv.invoice_number,
            "vendor_name": inv.vendor.name if inv.vendor else "Unknown",
            "invoice_date": str(inv.invoice_date),
            "subtotal": inv.subtotal,
            "total_amount": inv.total_amount,
            "status": inv.status
        })
    return results

@app.post("/audit/run")
def run_audit(req: AuditRequest, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.invoice_id == req.invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    po = inv.purchase_order
    vendor = inv.vendor
    
    score = anomaly_engine.score_invoice(
        subtotal=inv.subtotal,
        total_amount=inv.total_amount,
        amount_limit=po.amount_limit if po else 10000.0,
        risk_rating=vendor.risk_rating if vendor else 0.1
    )

    brief = agent_auditor.audit_invoice(invoice_id=inv.invoice_id, anomaly_score=score)
    return {
        "invoice_id": inv.invoice_id,
        "anomaly_score": round(score, 4),
        "audit_brief": brief
    }


# ---------------------------------------------------------------------------
# POST /audit — the real ReAct AuditAgent (core/agent/agentic_auditor.py)
#
# Takes a full invoice payload (invoice + PO + vendor + optional history) and
# runs the agent's ReAct loop over its deterministic tools, including the
# pgvector-backed find_similar_invoices / retrieve_policy tools when the DB
# is reachable.
#
# Latency note: the ReAct loop makes several sequential LLM calls, so a live
# audit can take 30-120s on the free-tier Gemini model. `run_agent_audit` is
# deliberately separated from the route handler so this exact function can
# later move into a background job / task queue without changing the API
# contract.
# ---------------------------------------------------------------------------
from core.agent.agentic_auditor import AuditAgent  # noqa: E402
from core.agent.llm_throttle import redact  # noqa: E402


class AuditLineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None
    po_unit_price: Optional[float] = None

    class Config:
        extra = "allow"


class AuditInvoice(BaseModel):
    invoice_id: str
    vendor_id: Optional[str] = None
    po_id: Optional[str] = None
    subtotal: float
    tax_amount: float
    total_amount: float
    invoice_date: Optional[str] = None
    line_items: List[AuditLineItem] = []
    amount_limit: Optional[float] = None
    risk_rating: Optional[float] = None
    raw_text: Optional[str] = None

    class Config:
        extra = "allow"


class AuditPO(BaseModel):
    po_id: Optional[str] = None
    amount_limit: Optional[float] = None

    class Config:
        extra = "allow"


class AuditVendor(BaseModel):
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None
    risk_rating: Optional[float] = 0.0
    on_file: Optional[bool] = True
    tax_id: Optional[str] = ""

    class Config:
        extra = "allow"


class AuditHistoryRow(BaseModel):
    invoice_id: Optional[str] = None
    vendor_id: Optional[str] = None
    po_id: Optional[str] = None
    total_amount: Optional[float] = None
    invoice_date: Optional[str] = None
    status: Optional[str] = None

    class Config:
        extra = "allow"


class AgentAuditRequest(BaseModel):
    invoice: AuditInvoice
    po: Optional[AuditPO] = None
    vendor: Optional[AuditVendor] = None
    history: List[AuditHistoryRow] = []

    class Config:
        extra = "allow"


def _ensure_agent_xgb_trained() -> None:
    """Make sure the tool-shared XGBoost engine is trained.

    Mirrors the eval harness: trains once per process on 20K synthetic
    invoices (same distribution as the proofs) so score_invoice_xgb returns a
    real score instead of a 'model not trained' error. Training takes ~10s
    and only runs if no model is loaded.
    """
    from core.agent import tools as agent_tools
    eng = agent_tools._get_engine()
    if eng.model is not None:
        return
    from scripts.synthesize import make_vendors, make_purchase_orders, make_invoices
    vendors = make_vendors()
    pos = make_purchase_orders(vendors)
    inv = make_invoices(vendors, pos, total=20000, seed=123)
    df = inv.merge(pos[["po_id", "amount_limit"]], on="po_id", how="left")
    df = df.merge(vendors[["vendor_id", "risk_rating"]], on="vendor_id", how="left")
    df["is_fraud"] = (df["status"] == "FLAGGED").astype(int)
    eng.train_dataframe(df)


def run_agent_audit(
    invoice: dict,
    po: dict | None = None,
    vendor: dict | None = None,
    history: list | None = None,
    llm_fn=None,
) -> dict:
    """Run the real ReAct audit agent and return the audit result dict.

    llm_fn is injectable for dry-run/smoke testing without Gemini quota
    (same pattern as the eval harness's --mock-llm); live calls need
    GEMINI_API_KEY. Raises RuntimeError (message redacted, no secrets) when
    the agent cannot run — callers must surface that as an honest error, never
    a fabricated verdict.
    """
    _ensure_agent_xgb_trained()

    if llm_fn is not None:
        agent = AuditAgent(llm_fn=llm_fn)
    else:
        try:
            agent = AuditAgent()  # reads GEMINI_API_KEY; free-tier model enforced
        except Exception as e:  # noqa: BLE001 - surfaced honestly by the caller
            raise RuntimeError(
                f"audit unavailable: {redact(e)[:300]}"
            ) from e

    try:
        out = agent.audit(invoice, po=po, vendor=vendor, history=history or [])
    except Exception as e:  # noqa: BLE001 - LLM failure: no verdict invented
        raise RuntimeError(f"audit failed: {redact(e)[:300]}") from e

    # --- degradation: which tools could not contribute evidence? ---
    unavailable_tools: list[str] = []
    tool_errors: list[str] = []
    tool_results: dict[str, dict] = {}
    policy_citations: list[dict] = []
    for entry in agent.trace:
        action = entry.get("action")
        obs = entry.get("observation")
        if not action or not isinstance(obs, dict):
            continue
        tool_results[action] = obs
        if obs.get("available") is False:
            # Graceful degradation (e.g. retrieval tools with no DB): not a crash.
            if action not in unavailable_tools:
                unavailable_tools.append(action)
        elif obs.get("error"):
            tool_errors.append(f"{action}: {str(obs['error'])[:200]}")
        if action == "retrieve_policy" and obs.get("available"):
            for p in obs.get("policies", []):
                policy_citations.append({
                    "section": p.get("section"),
                    "title": p.get("title"),
                    "fraud_classes": p.get("fraud_classes"),
                    "cosine_distance": p.get("cosine_distance"),
                })

    return {
        "invoice_id": invoice.get("invoice_id"),
        "verdict": out["verdict"],
        "confidence": out["confidence"],
        "findings": out["findings"],
        "amount_at_risk": out["amount_at_risk"],
        "steps": out["steps"],
        "model": out["model"],
        # degraded when any tool could not contribute evidence — whether it
        # reported itself unavailable (retrieval tools) or raised an error.
        "degraded": bool(unavailable_tools or tool_errors),
        "unavailable_tools": unavailable_tools,
        "tool_errors": tool_errors,
        "policy_citations": policy_citations,
        "tool_results": tool_results,
        # Trace: per-call latencies, call counts, token usage, cost (free tier).
        "trace": out["trace"],
        "note": (
            "Audit performed by the ProcureAI ReAct agent (deterministic tools + "
            "free-tier LLM reasoning). Latency is 30-120s per invoice on the "
            "free tier; treat this endpoint as synchronous for now — it is "
            "structured for a background-job migration later."
        ),
    }


@app.post("/audit")
def agent_audit(req: AgentAuditRequest):
    """Run the real ReAct audit agent on an invoice payload.

    Returns the verdict (APPROVE / FLAG / REJECT), evidence-backed findings,
    cited policy sections, per-tool observations, a degraded flag when the
    DB or a tool was unavailable, and a trace object with per-call latencies,
    LLM/tool call counts, token usage, and cost. Returns 503 (not a verdict)
    when the LLM backend cannot be reached — failures are honest errors,
    never fabricated.
    """
    try:
        return run_agent_audit(
            invoice=req.invoice.model_dump(),
            po=req.po.model_dump() if req.po else None,
            vendor=req.vendor.model_dump() if req.vendor else None,
            history=[h.model_dump() for h in req.history],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
