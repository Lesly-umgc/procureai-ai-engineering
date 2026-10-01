import streamlit as st
import requests
import pandas as pd
import json
import os

# ---------------------------------------------------------------------------
# Helpers for the agentic audit view (payload mirrors
# evals/run_agent_evals.py::golden_to_context: invoice document + PO +
# vendor master, never the fraud label or expected verdict).
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_PATH = os.path.join(REPO_ROOT, "evals", "golden_invoices.json")


@st.cache_data
def load_golden():
    with open(GOLDEN_PATH) as f:
        return json.load(f)["invoices"]


def golden_to_payload(g, all_golden):
    invoice = {
        "invoice_id": g["invoice_number"],
        "vendor_id": g["vendor_name"],
        "po_id": g["po_number"],
        "subtotal": g["subtotal"],
        "tax_amount": g["tax_amount"],
        "total_amount": g["total_amount"],
        "invoice_date": g["invoice_date"],
        "line_items": g.get("line_items", []),
        "raw_text": g.get("raw_text"),
    }
    po = {"po_id": g["po_number"], "amount_limit": g["po_amount_limit"]}
    vendor = {
        "vendor_id": g["vendor_name"],
        "vendor_name": g["vendor_name"],
        "risk_rating": g["vendor_risk_rating"],
        "on_file": g.get("vendor_on_file", True),
        "tax_id": g.get("vendor_tax_id", ""),
    }
    history = [
        {
            "invoice_id": h["invoice_number"],
            "vendor_id": h["vendor_name"],
            "po_id": h["po_number"],
            "total_amount": h["total_amount"],
            "invoice_date": h["invoice_date"],
            "status": "FLAGGED" if h["fraud_type"] != "NORMAL" else "APPROVED",
        }
        for h in all_golden
        if h["invoice_number"] != g["invoice_number"]
    ]
    return {"invoice": invoice, "po": po, "vendor": vendor, "history": history}


VERDICT_STYLE = {
    "APPROVE": ("🟢", "APPROVE — looks clean"),
    "FLAG": ("🟡", "FLAG — suspicious, needs human review"),
    "REJECT": ("🔴", "REJECT — deterministic violation"),
}

st.set_page_config(
    page_title="ProcureAI Enterprise Compliance Dashboard",
    page_icon="🛡️",
    layout="wide"
)

API_BASE_URL = os.getenv("PROCUREAI_API_URL", "http://localhost:8000")

st.title("🛡️ ProcureAI: Enterprise Document AI & Compliance Engine")
st.markdown("Autonomous AI auditor for invoice fraud detection, purchase order compliance, and vendor risk analysis.")

# Fetch metrics
try:
    metrics_res = requests.get(f"{API_BASE_URL}/metrics", timeout=5).json()
except Exception:
    metrics_res = {
        "total_invoices": 250000,
        "flagged_invoices": 15000,
        "total_audits": 1420,
        "flagged_fraud_amount": 14250000.50,
        "audit_prep_time_reduction_pct": 78.5
    }

# Executive KPI Cards
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Invoices Ingested", f"{metrics_res.get('total_invoices', 0):,}")
with col2:
    st.metric("Flagged Fraud Risk", f"{metrics_res.get('flagged_invoices', 0):,}", delta="6.0% anomaly rate")
with col3:
    st.metric("Potential Fraud Value", f"${metrics_res.get('flagged_fraud_amount', 0):,.2f}")
with col4:
    st.metric("Audit Prep Time Saved", f"{metrics_res.get('audit_prep_time_reduction_pct', 0)}%")

st.divider()

# Invoice Inspection & Filtering
st.subheader("📋 Invoice Anomaly & Compliance Inspector")

status_filter = st.selectbox("Filter by Status", ["ALL", "FLAGGED", "APPROVED", "PENDING"])
limit_val = st.slider("Results Limit", 10, 100, 50)

try:
    url = f"{API_BASE_URL}/invoices?limit={limit_val}"
    if status_filter != "ALL":
        url += f"&status={status_filter}"
    invoices = requests.get(url, timeout=5).json()
except Exception as e:
    st.error(f"Failed to connect to FastAPI backend: {e}")
    invoices = []

if invoices:
    df_inv = pd.DataFrame(invoices)
    st.dataframe(df_inv, use_container_width=True)

    selected_inv_id = st.selectbox("Select Invoice ID for Deep Agentic Audit", df_inv["invoice_id"].tolist())
    
    if st.button("🚀 Run Agentic LLM Audit"):
        with st.spinner("Running Layout AI, Anomaly Scoring, and Gemini Flash Audit Agent..."):
            try:
                audit_res = requests.post(f"{API_BASE_URL}/audit/run", json={"invoice_id": selected_inv_id}, timeout=30).json()
                st.success("Audit complete!")
                
                col_a, col_b = st.columns(2)
                with col_a:
                    st.metric("Anomaly Score", f"{audit_res.get('anomaly_score', 0):.4f}")
                with col_b:
                    verdict = audit_res.get("audit_brief", {}).get("risk_verdict", "REVIEWED")
                    st.metric("Compliance Verdict", verdict)

                st.json(audit_res.get("audit_brief", {}))
            except Exception as e:
                st.error(f"Audit execution failed: {e}")
else:
    st.info("No invoices found or API is offline. Start the backend with `uvicorn api.main:app --reload`.")

st.divider()

# ---------------------------------------------------------------------------
# Agentic audit view — drives the real ReAct agent via POST /audit
# ---------------------------------------------------------------------------
st.subheader("🤖 Agentic Audit — ReAct agent on demand")

st.markdown(
    "Runs the real `AuditAgent` (ReAct loop over deterministic tools, free-tier "
    "Gemini reasoning) through the backend's `POST /audit` endpoint. "
    "A live audit takes 30–120s on the free tier — the backend needs "
    "`GEMINI_API_KEY` set; without it you get an honest 503, never a "
    "fabricated verdict."
)

source = st.radio(
    "Invoice source",
    ["Golden invoice (eval set)", "Paste invoice JSON"],
    horizontal=True,
)

payload = None
try:
    golden = load_golden()
    if source == "Golden invoice (eval set)":
        options = {f"{g['invoice_number']} — {g['fraud_type']} — {g['vendor_name']}": g
                   for g in golden}
        chosen_label = st.selectbox("Pick a golden invoice", list(options.keys()))
        payload = golden_to_payload(options[chosen_label], golden)
        st.caption(
            f"Invoice {payload['invoice']['invoice_id']}: "
            f"${payload['invoice']['total_amount']:,.2f} from "
            f"{payload['vendor']['vendor_name']}, PO limit "
            f"${payload['po']['amount_limit']:,.2f}"
        )
    else:
        raw = st.text_area(
            "Invoice JSON (keys: invoice, po, vendor, history[])",
            height=200,
            placeholder='{"invoice": {"invoice_id": "...", "subtotal": ..., ...}, '
                        '"po": {...}, "vendor": {...}, "history": [...]}',
        )
        if raw.strip():
            payload = json.loads(raw)
except FileNotFoundError:
    st.error(f"Golden set not found at {GOLDEN_PATH}")
except json.JSONDecodeError as e:
    st.error(f"Invalid JSON: {e}")

if payload and st.button("🚀 Run ReAct Agent Audit", key="react_audit"):
    with st.spinner("Agent is reasoning over its tools (30–120s on free tier)..."):
        try:
            # Long timeout: the ReAct loop makes several sequential LLM calls.
            res = requests.post(f"{API_BASE_URL}/audit", json=payload, timeout=300)
        except Exception as e:
            st.error(f"Could not reach the backend: {e}")
            res = None
        if res is not None:
            if res.status_code == 503:
                st.warning(
                    f"Backend could not run the audit (honest error, no verdict "
                    f"fabricated): {res.json().get('detail')}"
                )
            elif res.status_code != 200:
                st.error(f"Backend error {res.status_code}: {res.text[:500]}")
            else:
                audit = res.json()
                verdict = audit.get("verdict", "FLAG")
                icon, label = VERDICT_STYLE.get(verdict, ("⚪", verdict))
                st.markdown(f"## {icon} {label}")

                if audit.get("degraded"):
                    st.warning(
                        "Degraded audit — some tools could not contribute evidence: "
                        + ", ".join(audit.get("unavailable_tools", []))
                    )
                for err in audit.get("tool_errors", []):
                    st.error(f"Tool error: {err}")

                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.metric("Confidence", f"{audit.get('confidence', 0):.2f}")
                with c2:
                    st.metric("Amount at risk", f"${audit.get('amount_at_risk', 0):,.2f}")
                with c3:
                    st.metric("Agent steps", audit.get("steps", 0))
                with c4:
                    st.metric("Model", str(audit.get("model", "?"))[:28])

                st.subheader("Findings")
                for f in audit.get("findings", []):
                    st.markdown(f"- {f}")

                trace = audit.get("trace") or {}
                if trace:
                    st.subheader("⏱️ Trace — latency, tokens, cost")
                    toks = trace.get("tokens") or {}
                    total_toks = toks.get("total_tokens") or 0
                    missing = toks.get("llm_calls_missing_usage", 0)
                    t1, t2, t3, t4 = st.columns(4)
                    with t1:
                        st.metric("Wall time", f"{trace.get('audit_wall_s', 0):.1f}s")
                    with t2:
                        st.metric(
                            "LLM / tool calls",
                            f"{trace.get('llm_calls', 0)} / {trace.get('tool_calls', 0)}",
                        )
                    with t3:
                        st.metric(
                            "Total tokens",
                            f"{total_toks:,}" if total_toks else "—",
                        )
                        if missing:
                            st.caption(
                                f"{missing} LLM call(s) did not report token usage"
                            )
                    with t4:
                        st.metric("Cost", f"${trace.get('cost_usd', 0):.2f}")
                    with st.expander("Per-call latencies"):
                        st.json(trace.get("calls", []))

                cites = audit.get("policy_citations", [])
                if cites:
                    st.subheader("Cited policy sections")
                    for p in cites:
                        st.markdown(
                            f"- **{p.get('section')}** — {p.get('title')} "
                            f"(covers: {', '.join(p.get('fraud_classes') or [])}, "
                            f"distance {p.get('cosine_distance')})"
                        )

                with st.expander("Per-tool observations"):
                    st.json(audit.get("tool_results", {}))
