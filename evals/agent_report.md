# ProcureAI Agent Eval Report

Generated: 2026-09-21 07:01 UTC (run by evals/run_agent_evals.py, live mode)
Git commit: `b8d5b73`
Golden set: `30` invoices (CALC_DISCREPANCY: 5, DUPLICATE: 5, GHOST: 5, NORMAL: 5, PRICE_DRIFT: 5, SPLIT_PO: 5)
Responder: **live Gemini `gemini-3.5-flash-lite`** via `core/agent/agentic_auditor.py` (ReAct loop over deterministic tools)
Judge: **mock** (`JUDGE_MODE`)

## Headline metrics

- Verdict accuracy: **93.3%** (28/30; 95% Wilson CI 78.7%–98.2%)
- Mean findings recall: **0.900**
- Agent errors: **0** (each counted as a verdict miss)

## By fraud type

| fraud type | n | verdict accuracy | mean findings recall |
|---|---|---|---|
| CALC_DISCREPANCY | 5 | 100.0% | 0.900 |
| DUPLICATE | 5 | 100.0% | 0.600 |
| GHOST | 5 | 100.0% | 1.000 |
| NORMAL | 5 | 60.0% | 1.000 |
| PRICE_DRIFT | 5 | 100.0% | 1.000 |
| SPLIT_PO | 5 | 100.0% | 0.900 |

## Per-invoice results

| invoice | fraud type | expected | got | verdict match | findings recall | steps | notes |
|---|---|---|---|---|---|---|---|
| INV-3001 | NORMAL | APPROVE | APPROVE | yes | 1.00 | 6 | verdict match (APPROVE). 3/3 findings recalled |
| INV-3002 | NORMAL | APPROVE | FLAG | no | 1.00 | 6 | verdict mismatch: expected APPROVE, got FLAG. 3/3 findings recalled |
| INV-3003 | NORMAL | APPROVE | FLAG | no | 1.00 | 6 | verdict mismatch: expected APPROVE, got FLAG. 3/3 findings recalled |
| INV-3004 | NORMAL | APPROVE | APPROVE | yes | 1.00 | 6 | verdict match (APPROVE). 3/3 findings recalled |
| INV-3005 | NORMAL | APPROVE | APPROVE | yes | 1.00 | 7 | verdict match (APPROVE). 3/3 findings recalled |
| INV-3006 | DUPLICATE | FLAG | FLAG | yes | 0.50 | 6 | verdict match (FLAG). 1/2 findings recalled. missing: same vendor amount and date as prior invoice |
| INV-3007 | DUPLICATE | FLAG | FLAG | yes | 1.00 | 6 | verdict match (FLAG). 2/2 findings recalled |
| INV-3008 | DUPLICATE | FLAG | FLAG | yes | 0.50 | 7 | verdict match (FLAG). 1/2 findings recalled. missing: same vendor amount and date as prior invoice |
| INV-3009 | DUPLICATE | FLAG | FLAG | yes | 0.50 | 7 | verdict match (FLAG). 1/2 findings recalled. missing: same vendor amount and date as prior invoice |
| INV-3010 | DUPLICATE | FLAG | FLAG | yes | 0.50 | 6 | verdict match (FLAG). 1/2 findings recalled. missing: same vendor amount and date as prior invoice |
| INV-3011 | SPLIT_PO | FLAG | FLAG | yes | 1.00 | 6 | verdict match (FLAG). 2/2 findings recalled |
| INV-3012 | SPLIT_PO | FLAG | FLAG | yes | 1.00 | 6 | verdict match (FLAG). 2/2 findings recalled |
| INV-3013 | SPLIT_PO | FLAG | FLAG | yes | 1.00 | 6 | verdict match (FLAG). 2/2 findings recalled |
| INV-3014 | SPLIT_PO | FLAG | FLAG | yes | 1.00 | 6 | verdict match (FLAG). 2/2 findings recalled |
| INV-3015 | SPLIT_PO | FLAG | FLAG | yes | 0.50 | 7 | verdict match (FLAG). 1/2 findings recalled. missing: threshold proximity suggests approval-limit evasion |
| INV-3016 | PRICE_DRIFT | FLAG | FLAG | yes | 1.00 | 6 | verdict match (FLAG). 2/2 findings recalled |
| INV-3017 | PRICE_DRIFT | FLAG | FLAG | yes | 1.00 | 6 | verdict match (FLAG). 2/2 findings recalled |
| INV-3018 | PRICE_DRIFT | FLAG | FLAG | yes | 1.00 | 6 | verdict match (FLAG). 2/2 findings recalled |
| INV-3019 | PRICE_DRIFT | FLAG | FLAG | yes | 1.00 | 7 | verdict match (FLAG). 2/2 findings recalled |
| INV-3020 | PRICE_DRIFT | FLAG | FLAG | yes | 1.00 | 6 | verdict match (FLAG). 2/2 findings recalled |
| INV-3021 | GHOST | REJECT | REJECT | yes | 1.00 | 7 | verdict match (REJECT). 2/2 findings recalled |
| INV-3022 | GHOST | REJECT | REJECT | yes | 1.00 | 6 | verdict match (REJECT). 2/2 findings recalled |
| INV-3023 | GHOST | REJECT | REJECT | yes | 1.00 | 6 | verdict match (REJECT). 2/2 findings recalled |
| INV-3024 | GHOST | REJECT | REJECT | yes | 1.00 | 4 | verdict match (REJECT). 2/2 findings recalled |
| INV-3025 | GHOST | REJECT | REJECT | yes | 1.00 | 6 | verdict match (REJECT). 2/2 findings recalled |
| INV-3026 | CALC_DISCREPANCY | REJECT | REJECT | yes | 1.00 | 7 | verdict match (REJECT). 2/2 findings recalled |
| INV-3027 | CALC_DISCREPANCY | REJECT | REJECT | yes | 0.50 | 6 | verdict match (REJECT). 1/2 findings recalled. missing: stated tax $749.60 inconsistent with 8% of subtotal ($649.60) |
| INV-3028 | CALC_DISCREPANCY | REJECT | REJECT | yes | 1.00 | 6 | verdict match (REJECT). 2/2 findings recalled |
| INV-3029 | CALC_DISCREPANCY | REJECT | REJECT | yes | 1.00 | 6 | verdict match (REJECT). 2/2 findings recalled |
| INV-3030 | CALC_DISCREPANCY | REJECT | REJECT | yes | 1.00 | 6 | verdict match (REJECT). 2/2 findings recalled |

## Methodology & honesty notes

- Every number above comes from this run only; nothing is carried over from other reports or runs.
- Agent errors (exceptions, timeouts, unparseable output) are recorded as rows with `verdict_match: false` and recall 0.0 — they are never silently dropped.
- `GEMINI_MODEL` is restricted to the free-tier allowlist in `core/agent/llm_throttle.py`; a non-free model fails the run before any API call.
- LLM calls are paced to the free-tier rate limit with retries and a circuit breaker; all logged text is redacted so API keys cannot leak into this report.
- The mock judge is deterministic and rule-based; verdict aliases (APPROVED/FLAGGED/REJECTED) are normalized before comparison.

Compare with `evals/baseline_report.md` (legacy single-prompt auditor) to measure the agent's improvement.
