"""Honest eval gatekeeper for the NEW ReAct audit agent.

Evaluates ``core/agent/agentic_auditor.py`` (ReAct loop over deterministic
tools) on ``evals/golden_invoices.json`` and grades with the mock judge
(deterministic, zero credentials) by default.

Honesty rules enforced by this script:

* FREE-TIER ONLY: ``GEMINI_MODEL`` must be on the free-tier allowlist in
  ``core/agent/llm_throttle.py``; anything else fails fast.
* NO SILENT SKIPS: agent exceptions are recorded as failed rows
  (verdict_match=False, recall=0.0, error noted) and count against the gate.
  Errors beyond ``--max-errors`` (default 0) fail the gate.
* NO FABRICATED METRICS: ``evals/agent_report.md`` is written by a *live* run
  only. ``--mock-llm`` runs a deterministic dry-run that exercises the full
  pipeline without API calls; it is loudly labeled a harness self-test, is
  never a model evaluation, and never writes ``agent_report.md``.
* NO SECRETS: every string written to stdout, JSON, or markdown is passed
  through ``llm_throttle.redact()`` so API keys can never leak into reports
  (as happened once in ``evals/baseline_report.md``).
* GATE: exits 0 only if verdict accuracy >= ``--min-accuracy``,
  mean findings recall >= ``--min-recall``, and errors <= ``--max-errors``.
  Exit 1 = gate failed, exit 2 = config/usage error.

Usage:
    # credential-free harness self-test (CI-safe)
    .venv/bin/python evals/run_agent_evals.py --mock-llm

    # live agent eval (needs a key; writes evals/agent_report.md)
    GEMINI_API_KEY=<key> .venv/bin/python evals/run_agent_evals.py [--limit 6]

Every number in the report comes from THIS run; see the "Methodology" section
of the generated report.
"""
import argparse
import datetime
import json
import math
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVALS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
sys.path.insert(0, EVALS)

from judge import get_judge  # noqa: E402
from core.agent.agentic_auditor import AuditAgent  # noqa: E402
from core.agent import tools as agent_tools  # noqa: E402
from core.agent.llm_throttle import assert_free_tier, redact  # noqa: E402
from scripts.synthesize import (  # noqa: E402
    make_vendors,
    make_purchase_orders,
    make_invoices,
)

MOCK_BANNER = (
    "!!! HARNESS SELF-TEST (mock LLM) !!!\n"
    "The numbers below exercise the pipeline (loop -> tools -> judge -> gate)\n"
    "with a DETERMINISTIC canned LLM. They are NOT an evaluation of the agent\n"
    "and must never be quoted as agent metrics. No agent_report.md is written."
)

DEFAULT_RESULTS = os.path.join(EVALS, "agent_results.json")
DEFAULT_REPORT = os.path.join(EVALS, "agent_report.md")


# --------------------------------------------------------------------------- #
# deterministic mock LLM (dry-run only: exercises the ReAct loop, no API)
# --------------------------------------------------------------------------- #
def make_mock_llm():
    """Canned ReAct backend: calls verify_arithmetic, then a FLAG verdict.

    Deterministic per invoice (keyed on invoice total in the prompt). Never
    claims to evaluate the real model.
    """
    import re

    state = {"step": 0}

    def mock_llm(prompt: str) -> str:
        state["step"] += 1
        if state["step"] % 2 == 1:
            return json.dumps({
                "thought": "dry-run: gather deterministic arithmetic evidence first",
                "action": "verify_arithmetic",
                "action_input": {},
            })
        m = re.search(r'"total_amount":\s*([0-9.]+)', prompt)
        total = float(m.group(1)) if m else 0.0
        return json.dumps({
            "thought": "dry-run: arithmetic observed; issuing canned verdict",
            "verdict": "FLAG",
            "confidence": 0.5,
            "findings": ["dry-run mock verdict; not a model output"],
            "amount_at_risk": total,
        })

    return mock_llm


# --------------------------------------------------------------------------- #
# eval machinery
# --------------------------------------------------------------------------- #
def train_xgb_for_tools():
    """Train the XGBoost model backing score_invoice_xgb (synthetic data)."""
    vendors = make_vendors()
    pos = make_purchase_orders(vendors)
    inv = make_invoices(vendors, pos, total=20000, seed=123)
    df = inv.merge(pos[["po_id", "amount_limit"]], on="po_id", how="left")
    df = df.merge(vendors[["vendor_id", "risk_rating"]], on="vendor_id", how="left")
    df["is_fraud"] = (df["status"] == "FLAGGED").astype(int)
    eng = agent_tools._get_engine()
    eng.train_dataframe(df)
    return eng


def golden_to_context(g: dict, all_golden: list) -> dict:
    # Only fields a real auditor would see: the invoice document (with line
    # items), the PO, and the vendor-master lookup. Never the fraud label,
    # the expected verdict/findings, or the analyst notes.
    invoice = {
        "invoice_id": g["invoice_number"],
        "vendor_id": g["vendor_name"],
        "po_id": g["po_number"],
        "subtotal": g["subtotal"],
        "tax_amount": g["tax_amount"],
        "total_amount": g["total_amount"],
        "invoice_date": g["invoice_date"],
        "line_items": g.get("line_items", []),
    }
    po = {"po_id": g["po_number"], "amount_limit": g["po_amount_limit"]}
    vendor = {
        "vendor_id": g["vendor_name"],
        "vendor_name": g["vendor_name"],
        "risk_rating": g["vendor_risk_rating"],
        "on_file": g.get("vendor_on_file", True),
        "tax_id": g.get("vendor_tax_id", ""),
    }
    history = []
    for h in all_golden:
        if h["invoice_number"] == g["invoice_number"]:
            continue
        history.append({
            "invoice_id": h["invoice_number"],
            "vendor_id": h["vendor_name"],
            "po_id": h["po_number"],
            "total_amount": h["total_amount"],
            "invoice_date": h["invoice_date"],
            "status": "FLAGGED" if h["fraud_type"] != "NORMAL" else "APPROVED",
        })
    # Fidelity fix: duplicate invoices reference an ORIGINAL invoice
    # (duplicate_of) that was billed before and must exist in the AP history —
    # otherwise find_duplicates is tested on an impossible task. Reconstruct it
    # from the golden record's own fields (same vendor/PO/amount, one day
    # earlier). This is prior-system data, not the current invoice's label.
    for h in all_golden:
        if not h.get("duplicate_of"):
            continue
        orig_date = (
            datetime.datetime.strptime(h["invoice_date"], "%Y-%m-%d")
            - datetime.timedelta(days=1)
        ).strftime("%Y-%m-%d")
        history.append({
            "invoice_id": h["duplicate_of"],
            "vendor_id": h["vendor_name"],
            "po_id": h["po_number"],
            "total_amount": h["total_amount"],
            "invoice_date": orig_date,
            "status": "APPROVED",
        })
    return {"invoice": invoice, "po": po, "vendor": vendor, "history": history}


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO, capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for a proportion. Honest uncertainty bars."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def evaluate(golden: list, agent: AuditAgent, judge, mock_mode: bool) -> list:
    """Run the agent over the golden set. Failures become failed rows, never skips."""
    rows = []
    for g in golden:
        ctx = golden_to_context(g, golden)
        row = {
            "invoice_number": g["invoice_number"],
            "fraud_type": g["fraud_type"],
            "expected_verdict": g["expected_verdict"],
            "verdict": None,
            "verdict_match": False,
            "findings_recall": 0.0,
            "steps": 0,
            "error": None,
            "notes": "",
        }
        try:
            out = agent.audit(ctx["invoice"], po=ctx["po"], vendor=ctx["vendor"],
                              history=ctx["history"])
            findings_text = " ".join(out["findings"])
            brief = {"risk_verdict": out["verdict"],
                     "summary": findings_text,
                     "reasoning": findings_text,
                     "findings": out["findings"]}
            expected = {"expected_verdict": g["expected_verdict"],
                        "expected_findings": g["expected_findings"]}
            score = judge.score(expected, brief)
            row.update(
                verdict=out["verdict"],
                verdict_match=bool(score["verdict_match"]),
                findings_recall=float(score["findings_recall"]),
                steps=int(out.get("steps", 0)),
                notes=redact(score.get("notes", "")),
            )
        except Exception as e:  # failure is data: counted as a miss, never skipped
            row["error"] = redact(f"{type(e).__name__}: {e}")[:300]
            row["notes"] = f"agent error: {row['error']}"
        ok = "OK" if row["verdict_match"] else "MISS"
        print(redact(
            f"{g['invoice_number']} {g['fraud_type']:<16} "
            f"exp={g['expected_verdict']:<7} got={str(row['verdict']):<7} {ok} "
            f"recall={row['findings_recall']:.2f} steps={row['steps']}"
            + (f" ERROR {row['error']}" if row["error"] else "")
        ), flush=True)
        rows.append(row)
    return rows


def summarize(rows: list) -> dict:
    n = len(rows)
    hits = sum(1 for r in rows if r["verdict_match"])
    errors = sum(1 for r in rows if r["error"])
    acc = hits / n if n else 0.0
    recall = sum(r["findings_recall"] for r in rows) / n if n else 0.0
    lo, hi = wilson_ci(hits, n)
    by_type: dict[str, dict] = {}
    for r in rows:
        t = by_type.setdefault(r["fraud_type"], {"n": 0, "hits": 0, "recall": 0.0})
        t["n"] += 1
        t["hits"] += r["verdict_match"]
        t["recall"] += r["findings_recall"]
    for t in by_type.values():
        t["accuracy"] = t["hits"] / t["n"] if t["n"] else 0.0
        t["mean_recall"] = t["recall"] / t["n"] if t["n"] else 0.0
        del t["recall"]
    return {
        "n": n,
        "verdict_accuracy": acc,
        "accuracy_ci_low": lo,
        "accuracy_ci_high": hi,
        "mean_findings_recall": recall,
        "errors": errors,
        "by_type": by_type,
    }


def render_report(meta: dict, rows: list, summary: dict) -> str:
    s = summary
    if meta["judge"] == "mock":
        judge_note = (
            "- The mock judge is deterministic and rule-based; verdict aliases "
            "(APPROVED/FLAGGED/REJECTED) are normalized before comparison."
        )
    else:
        judge_note = (
            "- The judge is a live LLM (Gemini, same model family as the agent) "
            "grading each brief against the golden record; verdict aliases "
            "(APPROVED/FLAGGED/REJECTED) are normalized before comparison. "
            "Same-family judging can favor the agent's phrasing, so treat this "
            "as a secondary signal alongside the mock-judge run."
        )
    lines = [
        "# ProcureAI Agent Eval Report",
        "",
        f"Generated: {meta['generated_utc']} (run by evals/run_agent_evals.py, live mode)",
        f"Git commit: `{meta['git_commit']}`",
        f"Golden set: `{s['n']}` invoices "
        f"({', '.join(f'{t}: {d['n']}' for t, d in sorted(s['by_type'].items()))})",
        f"Responder: **live Gemini `{meta['model']}`** via `core/agent/agentic_auditor.py` "
        "(LangGraph ReAct agent: `reason` -> `act` -> `finalize` graph over "
        "deterministic tools)",
        f"Judge: **{meta['judge']}** (`JUDGE_MODE`)",
        "",
        "## Headline metrics",
        "",
        f"- Verdict accuracy: **{s['verdict_accuracy']:.1%}** "
        f"({sum(1 for r in rows if r['verdict_match'])}/{s['n']}; "
        f"95% Wilson CI {s['accuracy_ci_low']:.1%}–{s['accuracy_ci_high']:.1%})",
        f"- Mean findings recall: **{s['mean_findings_recall']:.3f}**",
        f"- Agent errors: **{s['errors']}** (each counted as a verdict miss)",
        "",
        "## By fraud type",
        "",
        "| fraud type | n | verdict accuracy | mean findings recall |",
        "|---|---|---|---|",
    ]
    for t, d in sorted(s["by_type"].items()):
        lines.append(
            f"| {t} | {d['n']} | {d['accuracy']:.1%} | {d['mean_recall']:.3f} |"
        )
    lines += [
        "",
        "## Per-invoice results",
        "",
        "| invoice | fraud type | expected | got | verdict match | findings recall | steps | notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['invoice_number']} | {r['fraud_type']} | {r['expected_verdict']} | "
            f"{r['verdict']} | {'yes' if r['verdict_match'] else 'no'} | "
            f"{r['findings_recall']:.2f} | {r['steps']} | {r['notes']} |"
        )
    lines += [
        "",
        "## Methodology & honesty notes",
        "",
        "- Every number above comes from this run only; nothing is carried over "
        "from other reports or runs.",
        "- Agent errors (exceptions, timeouts, unparseable output) are recorded "
        "as rows with `verdict_match: false` and recall 0.0 — they are never "
        "silently dropped.",
        "- `GEMINI_MODEL` is restricted to the free-tier allowlist in "
        "`core/agent/llm_throttle.py`; a non-free model fails the run before "
        "any API call.",
        "- LLM calls are paced to the free-tier rate limit with retries and a "
        "circuit breaker; all logged text is redacted so API keys cannot leak "
        "into this report.",
        judge_note,
        "",
        "Compare with `evals/baseline_report.md` (legacy single-prompt auditor) "
        "to measure the agent's improvement.",
        "",
    ]
    return redact("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description="Honest eval gatekeeper for the ReAct audit agent.")
    ap.add_argument("--limit", type=int, default=0, help="only first N invoices (0=all)")
    ap.add_argument("--min-accuracy", type=float, default=0.80,
                    help="gate: minimum verdict accuracy (default 0.80)")
    ap.add_argument("--min-recall", type=float, default=0.0,
                    help="gate: minimum mean findings recall (default 0.0)")
    ap.add_argument("--max-errors", type=int, default=0,
                    help="gate: maximum tolerated agent errors (default 0)")
    ap.add_argument("--mock-llm", action="store_true",
                    help="dry-run with a deterministic canned LLM (no API calls, "
                         "no key needed). Harness self-test only; never writes agent_report.md.")
    ap.add_argument("--results", default=DEFAULT_RESULTS,
                    help="where to write per-invoice JSON results")
    ap.add_argument("--report", default=DEFAULT_REPORT,
                    help="where to write agent_report.md (live mode only)")
    ap.add_argument("--no-report", action="store_true", help="skip writing the report")
    args = ap.parse_args()

    mock_mode = args.mock_llm

    # --- config checks: fail fast, loudly ---
    if not mock_mode and not os.getenv("GEMINI_API_KEY"):
        print(redact("ERROR: GEMINI_API_KEY is not set. Live agent eval needs a key.\n"
                     "Run with --mock-llm for a credential-free harness self-test."), file=sys.stderr)
        return 2
    try:
        model = assert_free_tier(os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"))
    except Exception as e:
        print(redact(f"ERROR: {e}"), file=sys.stderr)
        return 2
    try:
        judge = get_judge()
    except Exception as e:
        print(redact(f"ERROR: {e}"), file=sys.stderr)
        return 2
    if mock_mode:
        print(MOCK_BANNER, flush=True)

    print("Training XGBoost for agent tools...")
    train_xgb_for_tools()

    golden = json.load(open(os.path.join(EVALS, "golden_invoices.json")))["invoices"]
    if args.limit:
        golden = golden[:args.limit]
    print(f"Evaluating {len(golden)} invoices with model={model} "
          f"judge={judge.name} llm_backend={'mock' if mock_mode else 'live'}...")

    agent = AuditAgent(llm_fn=make_mock_llm()) if mock_mode else AuditAgent()
    rows = evaluate(golden, agent, judge, mock_mode)
    summary = summarize(rows)

    meta = {
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "git_commit": git_commit(),
        "model": model,
        "judge": judge.name,
        "llm_backend": "mock" if mock_mode else "live",
        "free_tier_enforced": True,
        "limits": {"min_accuracy": args.min_accuracy, "min_recall": args.min_recall,
                   "max_errors": args.max_errors},
    }
    payload = {"meta": meta, "summary": summary, "rows": rows}
    results_text = redact(json.dumps(payload, indent=2))
    with open(args.results, "w") as f:
        f.write(results_text)
    print(f"\nWrote results JSON: {args.results}")

    # --- report: live mode only (mock runs must never mint an agent report) ---
    if not mock_mode and not args.no_report:
        report_text = render_report(meta, rows, summary)
        with open(args.report, "w") as f:
            f.write(report_text)
        print(f"Wrote agent report: {args.report}")
    elif mock_mode:
        print("\n(mock mode: agent_report.md NOT written — see banner above)")

    # --- gate ---
    failures = []
    if summary["verdict_accuracy"] < args.min_accuracy:
        failures.append(
            f"accuracy {summary['verdict_accuracy']:.1%} < --min-accuracy {args.min_accuracy:.0%}")
    if summary["mean_findings_recall"] < args.min_recall:
        failures.append(
            f"recall {summary['mean_findings_recall']:.3f} < --min-recall {args.min_recall}")
    if summary["errors"] > args.max_errors:
        failures.append(
            f"errors {summary['errors']} > --max-errors {args.max_errors}")

    print(f"\nAgent eval: {summary['n']} invoices | "
          f"verdict accuracy {summary['verdict_accuracy']:.1%} "
          f"(CI {summary['accuracy_ci_low']:.1%}–{summary['accuracy_ci_high']:.1%}) | "
          f"mean recall {summary['mean_findings_recall']:.3f} | "
          f"errors {summary['errors']}")
    if failures:
        print("GATE: FAIL — " + "; ".join(failures))
        return 1
    print("GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
