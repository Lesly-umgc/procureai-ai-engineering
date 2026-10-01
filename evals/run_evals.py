"""Run the ProcureAI golden-set evals against the CURRENT core/agent_auditor.py logic.

No Postgres required. ``database.db`` (plus ``dotenv``/``tenacity``) are stubbed
in ``sys.modules`` before import, and only ``AgentAuditor.call_gemini_api(prompt)``
is exercised -- the DB-touching ``audit_invoice()`` path is not used. The prompt
built per invoice mirrors the one in ``AgentAuditor.audit_invoice`` exactly
(same sections, same requested JSON schema).

Two responder modes:
* GEMINI_API_KEY set  -> the real ``AgentAuditor.call_gemini_api`` (live LLM).
* otherwise           -> a deterministic canned responder (rule-based on the
  invoice record) so the whole harness -- prompt build, brief parsing, judging,
  reporting -- is testable end-to-end with zero credentials.

The canned responder is a *test double*, not the system under test: a high mock
score means the plumbing works, not that the LLM is good. The honest baseline
for the current agent requires a real key.

Judging: JUDGE_MODE=mock (default, rule-based) | gemini (LLM judge, needs key).

Outputs: summary table on stdout + evals/baseline_report.md.
"""

import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
sys.path.insert(0, str(EVALS_DIR))  # for judge.py

from judge import get_judge  # noqa: E402


# ---------------------------------------------------------------------------
# Dependency stubs: run with stdlib + requests only, no Postgres, no dotenv,
# no tenacity, no sqlalchemy.
# ---------------------------------------------------------------------------
def _install_stubs():
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = dotenv

    tenacity = types.ModuleType("tenacity")

    def _retry(*dargs, **dkwargs):
        def deco(fn):
            return fn

        return deco

    tenacity.retry = _retry
    tenacity.stop_after_attempt = lambda n: ("stop_after_attempt", n)
    tenacity.wait_exponential = lambda **k: ("wait_exponential", k)
    sys.modules["tenacity"] = tenacity

    # Stub the whole `database` package so the real database/db.py is never
    # imported (it needs sqlalchemy + a live Postgres).
    db_pkg = types.ModuleType("database")
    db_pkg.__path__ = []
    sys.modules["database"] = db_pkg
    db_mod = types.ModuleType("database.db")

    class _Dummy:
        def __init__(self, *a, **k):
            pass

    db_mod.SessionLocal = _Dummy
    db_mod.Invoice = _Dummy
    db_mod.AuditLog = _Dummy
    sys.modules["database.db"] = db_mod


_install_stubs()

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "core.agent_auditor", REPO_ROOT / "core" / "agent_auditor.py"
)
_auditor_mod = importlib.util.module_from_spec(_spec)
sys.modules["core.agent_auditor"] = _auditor_mod
_spec.loader.exec_module(_auditor_mod)
AgentAuditor = _auditor_mod.AgentAuditor


# ---------------------------------------------------------------------------
# Prompt construction: mirrors AgentAuditor.audit_invoice's prompt format.
# ---------------------------------------------------------------------------
def build_prompt(inv, anomaly_score):
    return f"""
            You are ProcureAI Enterprise Compliance Auditor. Analyze the following high-risk invoice and produce a strict JSON audit brief.

            Invoice Details:
            - Invoice Number: {inv['invoice_number']}
            - Date: {inv['invoice_date']}
            - Subtotal: ${inv['subtotal']}
            - Total Amount: ${inv['total_amount']}
            - Raw OCR Text: {inv.get('raw_text', '')}

            Purchase Order Terms:
            - PO Number: {inv['po_number']}
            - Amount Limit: ${inv['po_amount_limit']}
            - Department: {inv['department']}

            Vendor Details:
            - Vendor Name: {inv['vendor_name']}
            - Tax ID: {inv['vendor_tax_id']}
            - Risk Rating: {inv['vendor_risk_rating']}

            Model Anomaly Score: {anomaly_score:.2f}

            Provide a JSON response with keys:
            - "summary": string summary of findings
            - "cited_policy_rules": list of policy rules violated or verified
            - "risk_verdict": "APPROVE" | "FLAG" | "REJECT"
            - "reasoning": detailed multi-step compliance reasoning
            """


# Stand-in for the XGBoost anomaly score (the real one needs Postgres).
# Documented heuristic so the prompt's score field is plausible per fraud type.
HEURISTIC_SCORES = {
    "NORMAL": 0.12,
    "DUPLICATE": 0.72,
    "SPLIT_PO": 0.78,
    "PRICE_DRIFT": 0.66,
    "GHOST": 0.93,
    "CALC_DISCREPANCY": 0.88,
}


# ---------------------------------------------------------------------------
# Canned responder: deterministic test double for the LLM in mock mode.
# Applies straightforward rules to the invoice record and emits a brief in the
# exact schema the real auditor requests. High mock scores validate the
# harness plumbing, not the LLM.
# ---------------------------------------------------------------------------
def canned_brief(inv):
    n = inv["invoice_number"]
    v = inv["vendor_name"]
    total = inv["total_amount"]
    ft = inv["fraud_type"]

    if ft == "NORMAL":
        return {
            "summary": f"Invoice {n} from {v} for ${total:,.2f} passed all checks.",
            "cited_policy_rules": ["SEC-4.2: Purchase Order Threshold Limit (verified)"],
            "risk_verdict": "APPROVE",
            "reasoning": (
                f"Step 1: line totals reconcile with subtotal ${inv['subtotal']:,.2f}. "
                f"Step 2: total ${total:,.2f} is within PO amount limit "
                f"${inv['po_amount_limit']:,.2f}. Step 3: {v} is on the approved "
                "vendor list with a valid tax ID. No anomalies found."
            ),
        }

    if ft == "DUPLICATE":
        dup = inv["duplicate_of"]
        return {
            "summary": f"Invoice {n} appears to be a duplicate of {dup}.",
            "cited_policy_rules": ["FIN-2.1: Duplicate Invoice Detection"],
            "risk_verdict": "FLAG",
            "reasoning": (
                f"Step 1: invoice {n} is a duplicate of {dup}: same vendor ({v}), "
                f"same amount ${total:,.2f}, and near-identical date. "
                "Step 2: same vendor amount and date as prior invoice indicates "
                "double billing. Flagged for manual review before payment."
            ),
        }

    if ft == "SPLIT_PO":
        related = ", ".join(inv.get("related_invoices", [])) or "no visible pair"
        return {
            "summary": f"Invoice {n} total ${total:,.2f} sits just below the $10,000 approval threshold.",
            "cited_policy_rules": ["SEC-4.2: Purchase Order Threshold Limit"],
            "risk_verdict": "FLAG",
            "reasoning": (
                f"Step 1: total ${total:,.2f} is just below the $10,000 approval "
                f"threshold. Step 2: possible split billing with {related} "
                "suggests the amount was engineered to evade sign-off. "
                "Step 3: threshold proximity anomaly warrants review."
            ),
        }

    if ft == "PRICE_DRIFT":
        parts = []
        for li in inv["line_items"]:
            pct = round((li["unit_price"] / li["po_unit_price"] - 1) * 100)
            parts.append(
                f"{li['description']}: billed ${li['unit_price']:,.2f} vs PO rate "
                f"${li['po_unit_price']:,.2f} ({pct}% above PO contracted rate)"
            )
        worst = max(
            inv["line_items"],
            key=lambda li: li["unit_price"] / li["po_unit_price"],
        )
        worst_pct = round((worst["unit_price"] / worst["po_unit_price"] - 1) * 100)
        return {
            "summary": f"Invoice {n} shows price inflation up to {worst_pct}% above PO rates.",
            "cited_policy_rules": ["FIN-3.4: Contracted Price Variance"],
            "risk_verdict": "FLAG",
            "reasoning": (
                "Step 1: compared billed unit prices against PO contracted rates: "
                + "; ".join(parts)
                + f". Step 2: price inflation on {worst['description']} exceeds "
                "tolerance. Flagged for procurement review."
            ),
        }

    if ft == "GHOST":
        return {
            "summary": f"Invoice {n} is from {v}, which is not in the approved vendor master.",
            "cited_policy_rules": ["VEN-1.1: Approved Vendor Requirement"],
            "risk_verdict": "REJECT",
            "reasoning": (
                f"Step 1: vendor {v} is not in the approved vendor master. "
                "Step 2: unregistered vendor with unverified tax ID "
                f"({inv['vendor_tax_id']}) and risk rating {inv['vendor_risk_rating']}. "
                "Step 3: payment to unregistered vendors is prohibited. Rejected."
            ),
        }

    if ft == "CALC_DISCREPANCY":
        line_sum = sum(li["line_total"] for li in inv["line_items"])
        sub, tax = inv["subtotal"], inv["tax_amount"]
        bits = []
        if abs(line_sum - sub) > 0.005:
            bits.append(
                f"line items sum to ${line_sum:,.2f} but stated subtotal is "
                f"${sub:,.2f} (calculation discrepancy of ${abs(line_sum - sub):,.2f})"
            )
        if abs(round(sub * 0.08, 2) - tax) > 0.005:
            bits.append(
                f"stated tax ${tax:,.2f} inconsistent with 8% of subtotal "
                f"(${round(sub * 0.08, 2):,.2f})"
            )
        if abs(sub + tax - total) > 0.011:
            bits.append(
                f"subtotal plus tax equals ${sub + tax:,.2f} but stated total is "
                f"${total:,.2f} (total mismatch of ${abs(sub + tax - total):,.2f})"
            )
        detail = "; ".join(bits) if bits else "totals do not reconcile"
        return {
            "summary": f"Invoice {n} has a calculation discrepancy: {detail}.",
            "cited_policy_rules": ["FIN-1.3: Invoice Arithmetic Integrity"],
            "risk_verdict": "REJECT",
            "reasoning": (
                f"Step 1: recomputed invoice arithmetic: {detail}. "
                "Step 2: the invoice as stated cannot be paid accurately. "
                "Rejected; vendor must correct and resubmit."
            ),
        }

    raise ValueError(f"unknown fraud_type {ft!r}")


# ---------------------------------------------------------------------------
# Eval run
# ---------------------------------------------------------------------------
def run():
    with open(EVALS_DIR / "golden_invoices.json") as f:
        invoices = json.load(f)["invoices"]

    use_real_llm = bool(os.getenv("GEMINI_API_KEY", ""))
    judge = get_judge()
    auditor = AgentAuditor() if use_real_llm else None

    results = []
    for inv in invoices:
        score = HEURISTIC_SCORES.get(inv["fraud_type"], 0.5)
        prompt = build_prompt(inv, score)
        try:
            raw = (
                auditor.call_gemini_api(prompt)
                if use_real_llm
                else json.dumps(canned_brief(inv))
            )
            brief = json.loads(raw)
            parse_error = None
        except Exception as e:  # bad JSON from the LLM still gets scored as a miss
            brief = {"risk_verdict": None, "summary": "", "cited_policy_rules": [], "reasoning": ""}
            parse_error = f"{type(e).__name__}: {e}"
        grading = judge.score(inv, brief)
        if parse_error:
            grading["notes"] = f"brief parse failed ({parse_error}). " + grading["notes"]
        results.append(
            {
                "invoice_number": inv["invoice_number"],
                "fraud_type": inv["fraud_type"],
                "expected": inv["expected_verdict"],
                "got": brief.get("risk_verdict"),
                **grading,
            }
        )

    # ---- aggregates ----
    n = len(results)
    verdict_acc = sum(r["verdict_match"] for r in results) / n
    mean_recall = sum(r["findings_recall"] for r in results) / n
    by_type = {}
    for r in results:
        t = by_type.setdefault(r["fraud_type"], {"n": 0, "match": 0, "recall": 0.0})
        t["n"] += 1
        t["match"] += r["verdict_match"]
        t["recall"] += r["findings_recall"]

    # ---- stdout summary ----
    print(f"\nProcureAI evals: {n} golden invoices | responder={'gemini-live' if use_real_llm else 'canned-mock'} | judge={judge.name}")
    print("-" * 96)
    print(f"{'invoice':<10} {'type':<16} {'expected':<8} {'got':<8} {'verdict':<7} {'recall':<6} notes")
    print("-" * 96)
    for r in results:
        vm = "OK " if r["verdict_match"] else "MISS"
        print(
            f"{r['invoice_number']:<10} {r['fraud_type']:<16} {r['expected']:<8} "
            f"{str(r['got']):<8} {vm:<7} {r['findings_recall']:<6.2f} {r['notes'][:60]}"
        )
    print("-" * 96)
    print(f"verdict accuracy : {verdict_acc:.1%} ({sum(r['verdict_match'] for r in results)}/{n})")
    print(f"mean findings recall: {mean_recall:.3f}")
    print("\nby fraud type:")
    for t, s in by_type.items():
        print(f"  {t:<16} acc {s['match']/s['n']:.1%}  recall {s['recall']/s['n']:.3f}  (n={s['n']})")

    # ---- markdown report ----
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# ProcureAI Eval Baseline Report",
        "",
        f"Generated: {ts}",
        f"Golden set: `{n}` invoices (5 per fraud type: NORMAL, DUPLICATE, SPLIT_PO, PRICE_DRIFT, GHOST, CALC_DISCREPANCY)",
        f"Responder: **{'live Gemini via core/agent_auditor.py' if use_real_llm else 'canned mock (rule-based test double, zero credentials)'}**",
        f"Judge: **{judge.name}** (`JUDGE_MODE`)",
        "",
        "## Headline metrics",
        "",
        f"- Verdict accuracy: **{verdict_acc:.1%}** ({sum(r['verdict_match'] for r in results)}/{n})",
        f"- Mean findings recall: **{mean_recall:.3f}**",
        "",
        "## By fraud type",
        "",
        "| fraud type | n | verdict accuracy | mean findings recall |",
        "|---|---|---|---|",
    ]
    for t, s in by_type.items():
        lines.append(f"| {t} | {s['n']} | {s['match']/s['n']:.1%} | {s['recall']/s['n']:.3f} |")
    lines += [
        "",
        "## Per-invoice results",
        "",
        "| invoice | fraud type | expected | got | verdict match | findings recall | notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        notes = r["notes"].replace("|", "\\|")
        lines.append(
            f"| {r['invoice_number']} | {r['fraud_type']} | {r['expected']} | {r['got']} "
            f"| {'yes' if r['verdict_match'] else 'no'} | {r['findings_recall']:.2f} | {notes} |"
        )
    lines += [
        "",
        "## What 'baseline' means",
        "",
    ]
    if use_real_llm:
        lines += [
            "This run used the **live Gemini model** through the current",
            "`AgentAuditor.call_gemini_api` (single prompt, no tools). These numbers are the",
            "honest baseline for the current agent: future changes (tool-using agent, better",
            "prompts, RAG) should be measured against them by re-running this harness.",
        ]
    else:
        lines += [
            "This run used the **canned mock responder**, a deterministic rule-based test",
            "double -- not the LLM. High scores here validate the harness plumbing",
            "(prompt construction, brief parsing, judging, reporting), not the agent.",
            "For the honest agent baseline, set `GEMINI_API_KEY` and re-run:",
            "",
            "```bash",
            "GEMINI_API_KEY=... JUDGE_MODE=gemini python evals/run_evals.py",
            "```",
            "",
            "Note: without a key, `AgentAuditor.call_gemini_api` itself returns a hardcoded",
            "`FLAGGED` mock brief (see core/agent_auditor.py); the canned responder is used",
            "instead so the harness exercises varied verdicts end-to-end.",
        ]
    lines += [
        "",
        "## Methodology",
        "See `evals/README.md` for golden-set design, judge scoring, and how to extend the set.",
        "",
    ]
    report_path = EVALS_DIR / "baseline_report.md"
    report_path.write_text("\n".join(lines))
    print(f"\nreport written: {report_path}")


if __name__ == "__main__":
    run()
