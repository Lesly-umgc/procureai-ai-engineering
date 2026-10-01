# ProcureAI Agent Eval Report

Generated: 2026-10-01 02:18 UTC (run by evals/run_agent_evals.py, live mode)
Git commit: `4afb13f`
Golden set: `30` invoices (CALC_DISCREPANCY: 5, DUPLICATE: 5, GHOST: 5, NORMAL: 5, PRICE_DRIFT: 5, SPLIT_PO: 5)
Responder: **live Gemini `gemini-3.5-flash-lite`** via `core/agent/agentic_auditor.py` (LangGraph ReAct agent: `reason` -> `act` -> `finalize` graph over deterministic tools)
Judge: **gemini** (`JUDGE_MODE`)

## Headline metrics

- Verdict accuracy: **93.3%** (28/30; 95% Wilson CI 78.7%–98.2%)
- Mean findings recall: **0.867**
- Agent errors: **1** (each counted as a verdict miss)

## By fraud type

| fraud type | n | verdict accuracy | mean findings recall |
|---|---|---|---|
| CALC_DISCREPANCY | 5 | 100.0% | 0.900 |
| DUPLICATE | 5 | 100.0% | 0.600 |
| GHOST | 5 | 100.0% | 1.000 |
| NORMAL | 5 | 60.0% | 0.800 |
| PRICE_DRIFT | 5 | 100.0% | 0.900 |
| SPLIT_PO | 5 | 100.0% | 1.000 |

## Per-invoice results

| invoice | fraud type | expected | got | verdict match | findings recall | steps | notes |
|---|---|---|---|---|---|---|---|
| INV-3001 | NORMAL | APPROVE | APPROVE | yes | 1.00 | 6 | The agent correctly matched the verdict and identified all expected findings in its brief. |
| INV-3002 | NORMAL | APPROVE | APPROVE | yes | 1.00 | 7 | The agent correctly matched the APPROVE verdict and successfully mentioned all three expected findings. |
| INV-3003 | NORMAL | APPROVE | FLAG | no | 1.00 | 6 | The agent correctly identified all expected findings, but the risk verdict did not match the expected APPROVE. |
| INV-3004 | NORMAL | APPROVE | None | no | 0.00 | 0 | agent error: RuntimeError: Gemini call failed after 4 attempts: 'parts' |
| INV-3005 | NORMAL | APPROVE | APPROVE | yes | 1.00 | 6 | The agent correctly matched the APPROVE verdict and successfully mentioned all expected findings. |
| INV-3006 | DUPLICATE | FLAG | FLAG | yes | 0.50 | 6 | The agent matched the verdict and identified the duplicate invoice finding, but missed the specific finding regarding the same vendor, amount, and date. |
| INV-3007 | DUPLICATE | FLAG | FLAG | yes | 0.50 | 7 | The agent matched the verdict and identified the duplicate invoice finding, but missed the specific finding regarding same vendor amount and date. |
| INV-3008 | DUPLICATE | FLAG | FLAG | yes | 0.50 | 6 | The agent matched the verdict and captured the duplicate invoice finding, but missed the specific finding regarding the same vendor amount and date. |
| INV-3009 | DUPLICATE | FLAG | FLAG | yes | 0.50 | 6 | The agent matched the verdict and identified the duplicate invoice finding, but missed the specific finding regarding the same vendor, amount, and date. |
| INV-3010 | DUPLICATE | FLAG | FLAG | yes | 1.00 | 7 | The agent correctly matched the FLAG verdict and successfully included all expected findings in its brief. |
| INV-3011 | SPLIT_PO | FLAG | FLAG | yes | 1.00 | 6 | The agent correctly matched the FLAG verdict and successfully mentioned all expected findings in its brief. |
| INV-3012 | SPLIT_PO | FLAG | FLAG | yes | 1.00 | 6 | The agent correctly matched the verdict and captured all expected findings in its brief. |
| INV-3013 | SPLIT_PO | FLAG | FLAG | yes | 1.00 | 6 | The agent correctly matched the verdict and identified all expected findings. |
| INV-3014 | SPLIT_PO | FLAG | FLAG | yes | 1.00 | 5 | The agent correctly matched the FLAG verdict and successfully captured all expected findings in its brief. |
| INV-3015 | SPLIT_PO | FLAG | FLAG | yes | 1.00 | 7 | The agent correctly matched the verdict and identified both expected findings regarding the threshold proximity. |
| INV-3016 | PRICE_DRIFT | FLAG | FLAG | yes | 1.00 | 7 | The agent correctly matched the verdict and identified all expected findings. |
| INV-3017 | PRICE_DRIFT | FLAG | FLAG | yes | 1.00 | 6 | The agent correctly matched the FLAG verdict and successfully identified all expected findings regarding unit price inflation. |
| INV-3018 | PRICE_DRIFT | FLAG | FLAG | yes | 1.00 | 6 | The agent correctly matched the verdict and identified all expected findings. |
| INV-3019 | PRICE_DRIFT | FLAG | FLAG | yes | 1.00 | 6 | The agent correctly matched the verdict and identified both expected findings in its brief. |
| INV-3020 | PRICE_DRIFT | FLAG | FLAG | yes | 0.50 | 6 | The agent matched the verdict and identified the unit price finding, but missed the price inflation on contract review hours finding. |
| INV-3021 | GHOST | REJECT | REJECT | yes | 1.00 | 6 | The agent correctly matched the rejection verdict and identified all expected findings in its brief. |
| INV-3022 | GHOST | REJECT | REJECT | yes | 1.00 | 6 | The agent correctly matched the rejection verdict and identified all expected findings. |
| INV-3023 | GHOST | REJECT | REJECT | yes | 1.00 | 6 | The agent correctly matched the verdict and identified all expected findings in its brief. |
| INV-3024 | GHOST | REJECT | REJECT | yes | 1.00 | 6 | The agent correctly matched the rejection verdict and successfully mentioned all expected findings. |
| INV-3025 | GHOST | REJECT | REJECT | yes | 1.00 | 6 | The agent matched the REJECT verdict and correctly identified all expected findings in its brief. |
| INV-3026 | CALC_DISCREPANCY | REJECT | REJECT | yes | 1.00 | 6 | The agent correctly matched the rejection verdict and successfully included both expected findings in its brief. |
| INV-3027 | CALC_DISCREPANCY | REJECT | REJECT | yes | 0.50 | 7 | The agent matched the verdict and identified the tax discrepancy, but missed the specific finding regarding the total being overstated by $100.00. |
| INV-3028 | CALC_DISCREPANCY | REJECT | REJECT | yes | 1.00 | 6 | The agent correctly matched the verdict and identified all expected calculation discrepancy findings. |
| INV-3029 | CALC_DISCREPANCY | REJECT | REJECT | yes | 1.00 | 6 | The agent matched the correct verdict and successfully recalled all expected findings in its brief. |
| INV-3030 | CALC_DISCREPANCY | REJECT | REJECT | yes | 1.00 | 5 | The agent correctly matched the rejection verdict and captured all expected discrepancy findings in its brief. |

## Methodology & honesty notes

- Every number above comes from this run only; nothing is carried over from other reports or runs.
- Agent errors (exceptions, timeouts, unparseable output) are recorded as rows with `verdict_match: false` and recall 0.0 — they are never silently dropped.
- `GEMINI_MODEL` is restricted to the free-tier allowlist in `core/agent/llm_throttle.py`; a non-free model fails the run before any API call.
- LLM calls are paced to the free-tier rate limit with retries and a circuit breaker; all logged text is redacted so API keys cannot leak into this report.
- The judge is a live LLM (Gemini, same model family as the agent) grading each brief against the golden record; verdict aliases (APPROVED/FLAGGED/REJECTED) are normalized before comparison. Same-family judging can favor the agent's phrasing, so treat this as a secondary signal alongside the mock-judge run.

Compare with `evals/baseline_report.md` (legacy single-prompt auditor) to measure the agent's improvement.
