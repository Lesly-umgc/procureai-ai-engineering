# ProcureAI Eval Baseline Report

Generated: 2026-09-20 05:49 UTC
Golden set: `30` invoices (5 per fraud type: NORMAL, DUPLICATE, SPLIT_PO, PRICE_DRIFT, GHOST, CALC_DISCREPANCY)
Responder: **live Gemini via core/agent_auditor.py**
Judge: **mock** (`JUDGE_MODE`)

## Headline metrics

- Verdict accuracy: **56.7%** (17/30)
- Mean findings recall: **0.300**

## By fraud type

| fraud type | n | verdict accuracy | mean findings recall |
|---|---|---|---|
| NORMAL | 5 | 100.0% | 0.400 |
| DUPLICATE | 5 | 100.0% | 0.400 |
| SPLIT_PO | 5 | 80.0% | 0.200 |
| PRICE_DRIFT | 5 | 0.0% | 0.000 |
| GHOST | 5 | 60.0% | 0.400 |
| CALC_DISCREPANCY | 5 | 0.0% | 0.400 |

## Per-invoice results

| invoice | fraud type | expected | got | verdict match | findings recall | notes |
|---|---|---|---|---|---|---|
| INV-3001 | NORMAL | APPROVE | APPROVE | yes | 0.33 | verdict match (APPROVE). 1/3 findings recalled. missing: line totals reconcile with subtotal; vendor on approved vendor list |
| INV-3002 | NORMAL | APPROVE | APPROVE | yes | 0.67 | verdict match (APPROVE). 2/3 findings recalled. missing: vendor on approved vendor list |
| INV-3003 | NORMAL | APPROVE | APPROVE | yes | 0.33 | verdict match (APPROVE). 1/3 findings recalled. missing: line totals reconcile with subtotal; vendor on approved vendor list |
| INV-3004 | NORMAL | APPROVE | APPROVE | yes | 0.33 | verdict match (APPROVE). 1/3 findings recalled. missing: line totals reconcile with subtotal; vendor on approved vendor list |
| INV-3005 | NORMAL | APPROVE | APPROVE | yes | 0.33 | verdict match (APPROVE). 1/3 findings recalled. missing: line totals reconcile with subtotal; vendor on approved vendor list |
| INV-3006 | DUPLICATE | FLAG | FLAG | yes | 0.00 | verdict match (FLAG). 0/2 findings recalled. missing: duplicate of INV-2025-0042133; same vendor amount and date as prior invoice |
| INV-3007 | DUPLICATE | FLAG | FLAG | yes | 0.50 | verdict match (FLAG). 1/2 findings recalled. missing: duplicate of INV-2025-0019871 |
| INV-3008 | DUPLICATE | FLAG | FLAG | yes | 0.50 | verdict match (FLAG). 1/2 findings recalled. missing: duplicate of INV-2025-0091234 |
| INV-3009 | DUPLICATE | FLAG | FLAG | yes | 0.50 | verdict match (FLAG). 1/2 findings recalled. missing: duplicate of INV-2025-0077665 |
| INV-3010 | DUPLICATE | FLAG | FLAG | yes | 0.50 | verdict match (FLAG). 1/2 findings recalled. missing: duplicate of INV-2025-0033110 |
| INV-3011 | SPLIT_PO | FLAG | FLAG | yes | 0.50 | verdict match (FLAG). 1/2 findings recalled. missing: possible split billing with INV-3012 |
| INV-3012 | SPLIT_PO | FLAG | FLAG | yes | 0.50 | verdict match (FLAG). 1/2 findings recalled. missing: possible split billing with INV-3011 |
| INV-3013 | SPLIT_PO | FLAG | FLAG | yes | 0.00 | verdict match (FLAG). 0/2 findings recalled. missing: total $9,890.55 just below $10,000 approval threshold; possible split billing with INV-3014 |
| INV-3014 | SPLIT_PO | FLAG | FLAG | yes | 0.00 | verdict match (FLAG). 0/2 findings recalled. missing: total $9,945.10 just below $10,000 approval threshold; possible split billing with INV-3013 |
| INV-3015 | SPLIT_PO | FLAG | None | no | 0.00 | brief parse failed (HTTPError: 503 Server Error: Service Unavailable for url: https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent ?key=<redacted>). verdict mismatch: expected FLAG, got None. 0/2 findings recalled. missing: total $9,975.00 just below $10,000 approval threshold; threshold proximity suggests approval-limit evasion |
| INV-3016 | PRICE_DRIFT | FLAG | None | no | 0.00 | brief parse failed (HTTPError: 503 Server Error: Service Unavailable for url: https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent ?key=<redacted>). verdict mismatch: expected FLAG, got None. 0/2 findings recalled. missing: unit price 50% above PO contracted rate; price inflation on cloud compute credits |
| INV-3017 | PRICE_DRIFT | FLAG | None | no | 0.00 | brief parse failed (HTTPError: 503 Server Error: Service Unavailable for url: https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent ?key=<redacted>). verdict mismatch: expected FLAG, got None. 0/2 findings recalled. missing: unit price 45% above PO contracted rate; price inflation on software licenses |
| INV-3018 | PRICE_DRIFT | FLAG | None | no | 0.00 | brief parse failed (HTTPError: 503 Server Error: Service Unavailable for url: https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent ?key=<redacted>). verdict mismatch: expected FLAG, got None. 0/2 findings recalled. missing: unit price 40% above PO contracted rate; price inflation on facility cleaning |
| INV-3019 | PRICE_DRIFT | FLAG | None | no | 0.00 | brief parse failed (HTTPError: 503 Server Error: Service Unavailable for url: https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent ?key=<redacted>). verdict mismatch: expected FLAG, got None. 0/2 findings recalled. missing: unit price 50% above PO contracted rate; price inflation on lab equipment calibration |
| INV-3020 | PRICE_DRIFT | FLAG | None | no | 0.00 | brief parse failed (HTTPError: 503 Server Error: Service Unavailable for url: https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent ?key=<redacted>). verdict mismatch: expected FLAG, got None. 0/2 findings recalled. missing: unit price 36% above PO contracted rate; price inflation on contract review hours |
| INV-3021 | GHOST | REJECT | None | no | 0.00 | brief parse failed (HTTPError: 429 Client Error: Too Many Requests for url: https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent ?key=<redacted>). verdict mismatch: expected REJECT, got None. 0/2 findings recalled. missing: vendor not in approved vendor master; unregistered vendor with unverified tax ID |
| INV-3022 | GHOST | REJECT | None | no | 0.00 | brief parse failed (HTTPError: 503 Server Error: Service Unavailable for url: https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent ?key=<redacted>). verdict mismatch: expected REJECT, got None. 0/2 findings recalled. missing: vendor not in approved vendor master; unregistered vendor with unverified tax ID |
| INV-3023 | GHOST | REJECT | REJECT | yes | 0.50 | verdict match (REJECT). 1/2 findings recalled. missing: vendor not in approved vendor master |
| INV-3024 | GHOST | REJECT | REJECT | yes | 0.50 | verdict match (REJECT). 1/2 findings recalled. missing: vendor not in approved vendor master |
| INV-3025 | GHOST | REJECT | REJECT | yes | 1.00 | verdict match (REJECT). 2/2 findings recalled |
| INV-3026 | CALC_DISCREPANCY | REJECT | FLAG | no | 0.00 | verdict mismatch: expected REJECT, got FLAG. 0/2 findings recalled. missing: line items sum to $5,000.00 but stated subtotal is $5,250.00; calculation discrepancy of $250.00 |
| INV-3027 | CALC_DISCREPANCY | REJECT | FLAG | no | 0.50 | verdict mismatch: expected REJECT, got FLAG. 1/2 findings recalled. missing: stated tax $749.60 inconsistent with 8% of subtotal ($649.60) |
| INV-3028 | CALC_DISCREPANCY | REJECT | FLAG | no | 0.50 | verdict mismatch: expected REJECT, got FLAG. 1/2 findings recalled. missing: subtotal plus tax equals $3,726.27 but stated total is $3,826.27 |
| INV-3029 | CALC_DISCREPANCY | REJECT | FLAG | no | 0.00 | verdict mismatch: expected REJECT, got FLAG. 0/2 findings recalled. missing: line items sum to $11,000.00 but stated subtotal is $10,800.00; calculation discrepancy of $200.00 |
| INV-3030 | CALC_DISCREPANCY | REJECT | FLAG | no | 1.00 | verdict mismatch: expected REJECT, got FLAG. 2/2 findings recalled |

## What 'baseline' means

This run used the **live Gemini model** through the current
`AgentAuditor.call_gemini_api` (single prompt, no tools). These numbers are the
honest baseline for the current agent: future changes (tool-using agent, better
prompts, RAG) should be measured against them by re-running this harness.

## Methodology
See `evals/README.md` for golden-set design, judge scoring, and how to extend the set.
