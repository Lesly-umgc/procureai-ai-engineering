# ProcureAI Evals

Phase-1 evaluation harness for the ProcureAI invoice auditor. It answers one
question reproducibly: **how good is the current auditor at catching fraud?**

## What the golden set covers

`golden_invoices.json` — 30 hand-built invoices, 5 per fraud type, each with a
known-correct verdict and the findings a good auditor should mention:

| fraud type | n | what's wrong | expected verdict |
|---|---|---|---|
| NORMAL | 5 | nothing; math reconciles, vendor registered, under PO limit | APPROVE |
| DUPLICATE | 5 | same vendor/amount/date as a prior invoice (`duplicate_of`) | FLAG |
| SPLIT_PO | 5 | totals $9,800–$9,999, just under the $10,000 sign-off limit | FLAG |
| PRICE_DRIFT | 5 | billed unit price 36–50% above the PO contracted rate | FLAG |
| GHOST | 5 | vendor not in the approved vendor master (`vendor_on_file: false`) | REJECT |
| CALC_DISCREPANCY | 5 | line totals / tax / total don't reconcile ($50–$250 off) | REJECT |

One NORMAL case (INV-3003, total $9,936) is deliberately near the $10,000 line
but legitimate — it tests that the auditor doesn't over-flag on threshold
proximity alone.

Each record carries everything the auditor's prompt needs (invoice fields, PO
terms, vendor details, line items with PO vs billed unit prices, simulated OCR
text) plus `expected_verdict`, `expected_findings`, and a `notes` field
explaining the setup.

## How the judge scores

`judge.py` — given the expected record and the agent's audit brief
(`summary`, `cited_policy_rules`, `risk_verdict`, `reasoning`), it returns:

- `verdict_match` (bool): agent's `risk_verdict` == expected verdict
  (aliases like APPROVED/FLAGGED/REJECTED are normalized).
- `findings_recall` (0–1): fraction of expected findings mentioned anywhere in
  the brief. A finding counts as recalled if ≥50% of its content keywords
  appear in the brief text.
- `notes` (str): what matched / missed.

Two implementations, selected by `JUDGE_MODE`:

- `mock` (default): deterministic rule-based scoring above. Zero credentials,
  zero cost. Use it for harness development and CI.
- `gemini`: an LLM judge via a raw `requests` call to
  `generativelanguage.googleapis.com` (no langchain). Requires
  `GEMINI_API_KEY`. More forgiving of paraphrase than keyword overlap.

## How to run

```bash
cd /path/to/ProcureAI

# Mock mode: zero credentials. Exercises the full pipeline
# (prompt build -> canned responder -> brief parsing -> judging -> report).
python evals/run_evals.py

# Real baseline: live Gemini as the auditor, mock judge
GEMINI_API_KEY=... python evals/run_evals.py

# Fully live: live Gemini as auditor AND judge
GEMINI_API_KEY=... JUDGE_MODE=gemini python evals/run_evals.py
```

`run_evals.py` needs **only stdlib + requests** — it stubs `database.db`
(no Postgres), `dotenv`, and `tenacity` in `sys.modules` before importing
`core/agent_auditor.py`, and only exercises `AgentAuditor.call_gemini_api(prompt)`
with a prompt that mirrors `audit_invoice`'s format exactly. The DB-backed
`audit_invoice()` path is intentionally not used.

Output: a summary table on stdout (per-invoice verdict/recal + aggregates) and
`evals/baseline_report.md`.

## Agent eval gatekeeper (`run_agent_evals.py`)

The ReAct agent (`core/agent/agentic_auditor.py`) is evaluated by a separate
harness that doubles as a CI gate:

```bash
# credential-free harness self-test (dry-run; NOT an agent evaluation)
.venv/bin/python evals/run_agent_evals.py --mock-llm

# live agent eval (needs key; writes evals/agent_results.json + evals/agent_report.md)
GEMINI_API_KEY=<key> .venv/bin/python evals/run_agent_evals.py [--limit 6]
```

Gate rules: exit 0 only if verdict accuracy >= `--min-accuracy` (default 80%),
mean findings recall >= `--min-recall`, and agent errors <= `--max-errors`
(default 0). Exit 1 = gate failed, exit 2 = config error (no key, non-free-tier
model, bad judge).

Honesty guarantees (enforced in code, not convention):

- **Free-tier only**: `GEMINI_MODEL` must be on the allowlist in
  `core/agent/llm_throttle.py`; anything else fails before any API call.
- **No silent skips**: agent exceptions become failed rows
  (`verdict_match: false`, recall 0.0) and count against the gate.
- **No fabricated reports**: `--mock-llm` is loudly labeled a harness
  self-test and never writes `evals/agent_report.md`; only a live run does.
- **No secret leaks**: all stdout/JSON/markdown output passes through
  `llm_throttle.redact()` (API keys can never land in reports again).
- Reports include a 95% Wilson CI on accuracy, per-type breakdown, git commit,
  model, and judge mode.

## What "baseline" means

- **Mock-mode numbers** validate the harness plumbing (prompt construction,
  JSON parsing, judging, reporting). The canned responder is a rule-based test
  double, so high mock scores say nothing about the LLM.
- **Live-key numbers** are the honest baseline for the current single-prompt
  auditor. Re-run after changes (tool-using agent, prompt edits, RAG) and
  compare: a change only counts as an improvement if these numbers go up.

## Extending the set

Add records to `golden_invoices.json` following the existing schema. Keep the
arithmetic honest: for non-`CALC_DISCREPANCY` invoices, line totals must sum to
the subtotal and subtotal + 8% tax must equal the total. For `CALC_DISCREPANCY`,
make exactly one of those inconsistent and describe it in `expected_findings`
and `notes`.
