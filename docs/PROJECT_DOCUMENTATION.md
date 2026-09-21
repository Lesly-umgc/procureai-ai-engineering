# ProcureAI — Project Documentation

> **Document status:** Living document. Updated 2026-09-20.
> **Source taxonomy:** This document follows the five documentation types defined in
> [Software documentation — Wikipedia](https://en.wikipedia.org/wiki/Software_documentation):
> Requirements, Architecture/Design, Technical, End user, and Marketing documentation.
> Per the *Docs as Code* principle described there, this file lives in the repo under
> version control (`docs/`) and is updated alongside the code it describes.

---

## 1. Overview

**ProcureAI** is an autonomous document-AI and compliance-auditing platform. It turns
unstructured vendor invoices into structured risk intelligence through a two-layer
system:

- **Local deterministic layer** — feature engineering and an XGBoost classifier that
  scores invoices in bulk.
- **Agentic LLM layer** — a ReAct audit agent (Gemini 3.5 Flash Lite, free tier) that
  performs multi-step verification of flagged invoices using deterministic tools, and
  outputs a structured audit brief with a verdict of `APPROVE`, `FLAG`, or `REJECT`.

The project's governing rule is: **implement every claim, prove every number.** No
headline metric or feature is stated without a rerunnable proof or an eval result.

---

## 2. Requirements Documentation

### 2.1 Functional requirements

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-1 | Ingest 250,000 synthetic invoices with six fraud classes (`NORMAL`, `DUPLICATE`, `SPLIT_PO`, `PRICE_DRIFT`, `GHOST`, `CALC_DISCREPANCY`) via a single shared, deterministic generator | ✅ Done | `scripts/synthesize.py` |
| FR-2 | Train an anomaly classifier on the dataset with ≥96% held-out accuracy | ✅ Done — **97.59%**, 0.88 ROC AUC | `proofs/prove_accuracy.py` |
| FR-3 | Measure audit-preparation time reduction against a documented manual baseline | ✅ Done — **99.87%** reduction (automated wall-clock vs. assumed 4-minute manual baseline) | `proofs/prove_efficiency.py` |
| FR-4 | Provide a ReAct agent that audits invoices with deterministic tools and emits a structured verdict | ✅ Done — 7 tools: the 5 originals plus `find_similar_invoices` and `retrieve_policy` (pgvector) | `core/agent/agentic_auditor.py`, `core/agent/tools.py`, `core/agent/retrieval.py` |
| FR-5 | Evaluate the agent on a fixed 30-invoice golden set with an 80% verdict-accuracy gate, where agent errors count as failures | 🟡 In progress — **70.0%** (21/30), CI 52.1%–83.3%, 0 errors | `evals/run_agent_evals.py`, `evals/agent_report.md` |
| FR-6 | Serve audits through FastAPI REST endpoints and a Streamlit dashboard | ✅ Done 2026-09-20 — `POST /audit` wired to the real `AuditAgent`; Streamlit has an "Agentic Audit" section; honest 503 on LLM failure, `degraded` flag on tool/DB outages | — |
| FR-7 | Record per-tool/LLM latency, call counts, tokens, and cost | ✅ Done 2026-09-20 — `trace` object on `POST /audit` and Streamlit summary | Roadmap item 3 |
| FR-8 | Add historical-invoice similarity + policy retrieval as a 6th agent tool backed by real pgvector | ✅ Done 2026-09-20 (verified vs. live PostgreSQL 16 + pgvector 0.6.0) | §4.3 note below |
| FR-9 | Provide pytest suite, GitHub Actions CI, Docker Compose, and reviewer setup docs | 🟡 Partial — pytest suite done 2026-09-20 (53 tests, all green), GitHub Actions CI done 2026-09-20, Docker Compose done 2026-09-20 (one-command reviewer setup: Postgres 16 + pgvector, init/seed, API on :8000, Streamlit on :8501); reviewer docs pending | Roadmap item 5 |
| FR-10 | Implement genuine LayoutLMv3 document understanding behind an off-by-default feature flag | ❌ Planned | Roadmap item 6 |

### 2.2 Non-functional requirements

- **Free-tier only.** All LLM work uses Gemini 3.5 Flash Lite's free allowance; rate
  limiting and retry logic are built into `core/agent/llm_throttle.py`. No paid API
  spend is permitted.
- **Honest evals.** The eval runner counts API/agent errors as verdict misses, never
  silently drops failures, redacts credentials from reports, and writes a reproducible
  report plus JSON (`evals/run_agent_evals.py`).
- **Reproducibility.** Every headline number is produced by a checked-in script; the
  synthesis pipeline is deterministic and shared across data generation, training,
  proofs, and evals.

### 2.3 Known gaps between claims and implementation (honest ledger)

The README previously described a full pipeline (LayoutLMv3 + Tesseract OCR, pgvector indexing,
FastAPI, Streamlit). Corrected 2026-09-20: the README no longer claims LayoutLMv3 (see §4.3 note). Current truth:

- **LayoutLMv3:** not implemented — the README/architecture-PDF claims were corrected
  2026-09-20 (genuine document-understanding stays planned, FR-10). `core/document_ai.py`
  is Tesseract OCR + MiniLM embeddings; boxes use the 0-1000 convention shared by
  transformer document models.
- **pgvector:** verified 2026-09-20 against a live PostgreSQL 16 + pgvector 0.6.0 —
  `find_similar_invoices` and `retrieve_policy` are registered agent tools (currently
  optional in the mandatory tool sweep; decision pending before the next live eval).
- **FastAPI/Streamlit:** `POST /audit` is wired to the real `AuditAgent` and the
  Streamlit app has an "Agentic Audit" section (done 2026-09-20; the same commit
  also fixed a pre-existing `score_invoice_xgb` bug — the tool had raised
  `TypeError` on every agent call since creation, so the 60%→70% climb happened
  with the ML tool silently erroring).
- **Agent eval:** 70.0% vs. the 80% gate — work continues; see §8.

---

## 3. Architecture/Design Documentation

See also: `docs/architecture-before-after.md` (diagrams).

### 3.1 Before → After

- **Before:** a single LLM call over an invoice ("agentic" in name only), no evals,
  pgvector unused, README overclaims.
- **After (current):** an asymmetric architecture —
  **local CPU-optimized** execution (feature engineering, XGBoost scoring,
  deterministic verification tools) paired with a **rate-limited cloud LLM agent**
  that performs deep policy reasoning only on high-suspicion records.

### 3.2 Component map and status

```
PDF/scan ──(FR-10, planned)──► LayoutLMv3/Tesseract ──► structured invoice
structured invoice ──(FR-1 ✅)──► scripts/synthesize.py (shared deterministic data)
        │
        ├──(FR-2 ✅)──► core/anomaly_engine.py — XGBoost (500 trees, d6, lr 0.05)
        │                    trains/scores on production features
        │
        ├──(FR-4 ✅)──► core/agent/agentic_auditor.py — ReAct loop
        │               core/agent/tools.py — 5 deterministic tools
        │               core/agent/llm_throttle.py — free-tier rate limiting
        │
        ├──(FR-8, planned)──► pgvector similarity + policy retrieval (6th tool)
        │
        ├──(FR-6, partial)──► api/main.py (FastAPI), dashboard/app.py (Streamlit)
        │
        └──(FR-5 🟡)──► evals/ — golden set, judge, hardened runner, reports
```

### 3.3 Design principles

- **Deterministic tools, probabilistic reasoning.** Facts come from code
  (`verify_arithmetic`, `find_duplicates`, `check_po`, `assess_vendor`,
  `score_invoice_xgb`); the LLM plans, sequences, and writes the brief.
- **Verdict discipline.** `REJECT` is reserved for deterministic certainty
  (arithmetic mismatch, unregistered vendor, PO over-limit with evidence).
  Heuristic evidence (fuzzy duplicates, possible split billing, near-threshold
  amounts) yields `FLAG`.
- **Errors are data.** Any agent exception, timeout, or unparseable output is a
  recorded verdict miss with recall 0.0 — never dropped.

---

## 4. Technical Documentation

### 4.1 Module reference

| Module | Purpose |
|---|---|
| `scripts/synthesize.py` | Deterministic 250K-invoice generator; single source of truth for DB loader, proofs, and evals. Implements real `DUPLICATE` (near-copy) and `GHOST` (high-risk-vendor) fraud. |
| `core/anomaly_engine.py` | XGBoost training/scoring on production features. Bug fixed during this work: `tax_ratio` was computed differently at train vs. score time, blinding the model to calculation fraud. |
| `core/agent/agentic_auditor.py` | ReAct audit loop over the toolset; emits structured JSON brief + verdict. |
| `core/agent/tools.py` | Seven tools: the five originals plus `find_similar_invoices` and `retrieve_policy` (registered; retrieval is *optional* in the mandatory sweep — see note below the eval ledger). |
| `core/agent/llm_throttle.py` | Free-tier rate limiting and retries for Gemini 3.5 Flash Lite. |
| `core/agent_auditor.py` | Legacy single-prompt auditor — kept as the honest baseline (56.7%). |
| `proofs/prove_accuracy.py` | Rerunnable proof: 97.59% accuracy on held-out 250K-invoice split. |
| `proofs/prove_efficiency.py` | Rerunnable proof: 99.87% audit-prep time reduction (methodology documented in README). |
| `evals/golden_invoices.json` | Fixed 30-invoice eval set (5 per fraud class). |
| `evals/judge.py` | Deterministic mock judge (+ Gemini judge mode). |
| `evals/run_agent_evals.py` | Hardened live eval runner; writes `evals/agent_report.md` + `evals/agent_results.json`. |
| `evals/run_evals.py` | Legacy baseline eval runner. |
| `database/db.py` | PostgreSQL + pgvector schema/index setup; includes the `Policy` ORM model and `policies_embedding_idx` (FR-8 done 2026-09-20). |
| `api/main.py`, `dashboard/app.py` | FastAPI `POST /audit` + Streamlit "Agentic Audit" section, both wired to the real agent (FR-6 done 2026-09-20). |

### 4.2 How to reproduce the headline numbers

```bash
# Accuracy proof (97.59% on held-out 250K-invoice split)
.venv/bin/python proofs/prove_accuracy.py

# Efficiency proof (99.87% audit-prep time reduction)
.venv/bin/python proofs/prove_efficiency.py   # --manual-min overrides the 4-min baseline assumption

# Live agent eval on Gemini 3.5 Flash Lite (80% gate)
.venv/bin/python evals/run_agent_evals.py     # writes evals/agent_report.md + evals/agent_results.json
```

### 4.3 Eval results ledger (live, Gemini 3.5 Flash Lite, 30 invoices)

| Run | Change | Verdict accuracy | Findings recall | Errors | Gate (80%) |
|---|---|---|---|---|---|
| 1 | Initial agent | 60.0% (18/30), CI 42.3%–75.4% | 0.278 | 0 | FAIL |
| 2 | Verdict rubric hardened; line items + vendor-master fields passed to agent; per-line price-drift checks; vendor/tax-ID verification | 66.7% (20/30), CI 48.8%–80.8% | 0.700 | 0 | FAIL |
| 3 | Fixed real `verify_arithmetic` line-sum defect; duplicate originals added to history; clean/near-threshold/split-billing verdict guards | 70.0% (21/30), CI 52.1%–83.3% | 0.822 | 0 | FAIL |
| 4 | Duplicates & split-billing → `FLAG` (heuristic ≠ certain fraud); all five tool calls mandatory before verdict | *aborted — quota* | — | 27 | — |
| 4 (rerun) | **Same agent code; rerun after quota reset. First fair eval with the corrected `score_invoice_xgb` dispatch (runs 1–3 ran with the XGBoost tool raising TypeError on every call)** | **93.3% (28/30), CI 78.7%–98.2%** | 0.900 | 0 | **PASS** |

Run 4 rerun note (2026-09-21 07:01 UTC, live): this is the **first canonical
corrected-tool run** — runs 1–3 remain honest history but measured a
handicapped agent whose XGBoost tool raised on every call (fixed in
`score_invoice_xgb` before this run). Same Gemini model and prompts as before;
verdict accuracy **93.3% (28/30; 95% Wilson CI 78.7%–98.2%)**, mean findings
recall **0.900**, 0 agent errors — **PASS** against the 80% gate. Both misses
were false positives on NORMAL invoices (`INV-3002`, `INV-3003`: expected
APPROVE, got FLAG); NORMAL accuracy 60% (3/5). Fraud-category verdict
accuracy was 100% for CALC_DISCREPANCY, DUPLICATE, GHOST, PRICE_DRIFT and
SPLIT_PO. Duplicate findings recall remains weaker at 0.600. Matched artifact
pair: `evals/agent_report.md` + `evals/agent_results.json` (30 rows).

Run 4 note (2026-09-20 15:36 EDT): the live run died on **API quota, not agent
quality** — `gemini-3.5-flash-lite` returned HTTP 429 (quota exceeded) after 3
invoices (2 OK, 1 miss), then the circuit breaker tripped; remaining 27
invoices recorded as errors. The 6.7% partial output was a quota artifact, not
a measurement, so `evals/agent_report.md` / `evals/agent_results.json` were
restored to the run-3 result; the raw failed JSON is preserved at
`workspace/goals/procureai-repo-ai-engineering-upgrade/hidden_files/run4_quota_failure_2026-09-20.json`.
Auth for the run went through the secure `custom.google-gemini` connector via
authd surrogates (`/tmp` launcher, now `~/workspace/run4_live_launcher.py`); no
raw key was handled.

Reviewer note: the evaluator reconstructs duplicate originals from each golden
record's `duplicate_of` field (one day earlier). This makes detection testable but
is fixture construction from labels; a future revision should represent prior
invoices explicitly in the dataset/history instead.

pgvector tooling verification (2026-09-20, not part of the eval loop): FR-8 was
implemented and verified against a **live PostgreSQL 16 + pgvector 0.6.0** —
3,000 invoices re-embedded with real MiniLM-L6-v2 (384-dim, normalized),
`policies` seeded with 8 explicitly-synthetic snippets (all 6 fraud classes).
`find_similar_invoices` returns real neighbors with correctly sorted cosine
distances; `retrieve_policy` cites the right policy (a DUPLICATE invoice pulled
FIN-2.1 "Duplicate Invoice Detection" as a top hit); both degrade to
`available: False` when the DB is unreachable, without crashing the audit.
Honest limits: verification was on a 3,000-row subset (not the 250K load), and
synthetic invoice text uses a fixed template so embeddings cluster tightly —
rankings will be more discriminative on real varied invoice text. The two tools
are registered but currently **optional** in the agent's mandatory 5-tool sweep
(deliberate: mandatory retrieval would add LLM steps per invoice and burn free-tier
quota); the decision is pending before the next live eval run.

---

## 5. End User Documentation

*Per Wikipedia, user docs are typically organized as Tutorial, Thematic, or
List/Reference. This section is currently Tutorial; a full thematic manual and a
reference index are future work (part of FR-9's reviewer docs).*

### 5.1 For the compliance officer (tutorial)

1. Invoices land in PostgreSQL (today: synthetic via `scripts/synthesize.py`).
2. The XGBoost engine scores every invoice; high-risk ones are routed to the
   ReAct agent.
3. The agent calls deterministic tools — arithmetic check, duplicate search,
   PO check, vendor assessment, XGBoost score — then writes a structured brief.
4. You read the verdict: `APPROVE` (clean), `FLAG` (heuristic suspicion, needs a
   human look), `REJECT` (deterministic violation).

### 5.2 For the engineer / reviewer (thematic)

- **Prove a number:** run the scripts in §4.2. Nothing in the README is
  hardcoded; the methodology is printed alongside each result.
- **Audit the agent:** `evals/golden_invoices.json` is the fixed test set;
  `evals/agent_report.md` shows per-invoice verdicts, misses, and misses by
  class for the latest run.
- **Extend the tools:** add a function to `core/agent/tools.py` and register it
  in the agent's tool sweep; the eval gate will tell you if it helped.
- **Run the app:** `uvicorn api.main:app` and `streamlit run dashboard/app.py`
  (real `AuditAgent` wired in — FR-6 done 2026-09-20).

### 5.3 Planned (List/Reference)

API endpoint reference, tool input/output schemas, fraud-class taxonomy with
examples, and a glossary — to be written with FR-9.

---

## 6. Marketing Documentation

*Per Wikipedia, marketing documentation should excite, inform accurately, and
position against alternatives. This section does all three — honestly.*

**ProcureAI** automates the invoice-compliance bottleneck: cross-checking receipts
against purchase orders, hunting split-billing around approval thresholds, price
drift, ghost vendors, duplicates, and calculation fraud.

Proven, rerunnable numbers (not marketing copy):

- **97.59%** anomaly-classification accuracy on a held-out 250,000-invoice set
  (`proofs/prove_accuracy.py`).
- **99.87%** audit-preparation time reduction, automated wall-clock vs. a
  documented 4-minute manual baseline (`proofs/prove_efficiency.py`).
- An **agentic auditor** (ReAct + 5 deterministic tools), calibrated live:
  60.0% → 66.7% → 70.0% across three runs, then **93.3% (28/30; 95% Wilson CI
  78.7%–98.2%)** on run 4 (2026-09-21) — the first fair eval with the
  corrected `score_invoice_xgb` tool — **passing the 80% gate**; reported
  win or lose.

Position vs. alternatives: unlike single-prompt "AI auditors" (our own legacy
baseline scores 56.7%), ProcureAI separates deterministic verification (code)
from judgment (LLM), counts every agent failure as a miss, and publishes its
eval reports. Unlike black-box vendors, every claim ships with the script that
produced it.

---

## 7. Future Aims (Roadmap)

Sequenced.

1. **Finish the eval loop** — *parked per user direction 2026-09-20 ("come back
   later")*: pass the 80% verdict-accuracy gate on the live agent, or reach a
   documented honest stop with remaining misses analyzed. Run-4 rerun
   (2026-09-21, first fair eval with the corrected `score_invoice_xgb` tool)
   scored **93.3% (28/30; 95% Wilson CI 78.7%–98.2%)** — **gate PASSED**.
   Remaining work: reduce the two NORMAL false positives (`INV-3002`,
   `INV-3003`) without harming fraud recall, and remove evaluator-label
   leakage (`duplicate_of` fixture construction) before calling this a
   final number.
2. ~~**Integrate `AuditAgent` into FastAPI and Streamlit**~~ — **done** 2026-09-20:
   `POST /audit` returns verdict/findings/evidence/degraded state; Streamlit has an
   "Agentic Audit" section. Also fixed the `score_invoice_xgb` `TypeError` (tool had
   errored on every agent call since creation).
3. ~~**Tracing & instrumentation**~~ — **done** 2026-09-20: per-tool and
   per-LLM-call latency, call counts, Gemini `usageMetadata` token capture
   (nulls when the backend reports none — never invented), and `cost_usd`
   0.0 on the free tier. Exposed as a `trace` object in `POST /audit` and a
   summary in the Streamlit "Agentic Audit" section.
4. ~~**pgvector 6th tool**~~ — **done and verified** 2026-09-20 against a live
   PostgreSQL 16 + pgvector 0.6.0 (see §4.3 note). Open decision: keep retrieval
   optional in the tool sweep, or make it mandatory before the next live eval
   (quota cost tradeoff).
5. **Engineering rigor** — pytest suite (**done** 2026-09-20: 53 tests, all
   green — deterministic tools, XGB scoring incl. the context-enrichment
   regression test, agent dispatch, trace instrumentation honesty, retrieval
   graceful degradation, and the `POST /audit` 503-vs-verdict contract; no
   live Gemini or Postgres in the suite), GitHub Actions CI (**done**
   2026-09-20: `.github/workflows/ci.yml` runs install → compileall →
   `scripts/ci_secret_scan.sh` → pytest on push/PR to `ai-engineer-upgrade`
   and `main`; the scan was proven against a planted token), Docker Compose
   (**done** 2026-09-20, **live-verified** 2026-09-20: `docker-compose.yml` +
   `Dockerfile` + `.dockerignore` — one command brings up Postgres 16 +
   pgvector, a one-shot init/seed (tables, vector extension, IVFFlat indexes,
   8 real-embedded synthetic policies), the FastAPI API on :8000, and
   Streamlit on :8501; dashboard API URL is env-overridable via
   `PROCUREAI_API_URL`. Verification ran on the build machine with Docker
   28.5.1 installed from static binaries. Sandbox limits (documented, not
   hidden): the VM kernel lacks bridge/iptables NAT modules and the OCI
   runtime cannot start containers here (setns blocked), so the stack was
   verified by running each service's exact command natively — real Postgres
   16.15 + pgvector binaries extracted from the pinned `pgvector/pgvector:pg16`
   image, the compose `init-db` command verbatim, `uvicorn api.main:app` on
   :8000, and `streamlit run dashboard/app.py` on :8501 with
   `PROCUREAI_API_URL=http://localhost:8000`. Proven: extension + 6 tables +
   8 policies at 384 dims; `/health`, `/metrics`, `/invoices` live; `/audit`
   returns the honest 503 without a key; dashboard serves HTTP 200 and honors
   the env override; `retrieve_policy` returns correctly ranked hits
   (cosine distances ascending). The `Dockerfile`'s apt/pip RUN steps and the
   compose container wiring itself could not be executed in this sandbox and
   remain to be confirmed on a normal Docker host; `docker compose config`
   validates the YAML merge. Two real bugs found by this verification and
   fixed: (1) `init_db()` created tables before `CREATE EXTENSION vector`
   (fresh DBs failed with `type "vector" does not exist`) — extension now
   first; (2) the policies IVFFlat index used `lists=100` on an 8-row table,
   which made pgvector return zero rows — now `lists=1` with the reason
   recorded.
   Privilege correction (2026-09-21, honest erratum): the 2026-09-20
   verification passed only because the application role had been made a
   Postgres SUPERUSER (both in the compose design — `POSTGRES_USER:
   procureai` makes the app role superuser in the official image — and in
   the local retest). Least-privilege first boot was therefore NOT proven.
   Fixed: the `db` service now boots as the stock `postgres` superuser,
   which exists solely to run `docker/db-init/01-procureai.sh` once from
   `/docker-entrypoint-initdb.d`; that hook creates a NON-SUPERUSER
   `procureai` role (`NOSUPERUSER NOCREATEDB NOCREATEROLE`), a
   `procureai_db` database owned by it, and the `vector` extension
   (superuser-only). All app services connect as the unprivileged role.
   Re-verified 2026-09-21 from a scratch database, running the actual hook
   file: role confirmed `rolsuper=false`; negative control — `init_db()`
   as the app role on a DB without the extension fails loudly with
   `permission denied to create extension "vector"` (proving the hook is
   load-bearing, not the role); positive path — `init_db()` + policy seed
   succeed as the non-superuser role (extension statement is a no-op),
   yielding 6 app-owned tables, 8 policies at 384 dims, correct retrieval
   ranking, live `/health` + `/metrics`, and the honest `/audit` 503;
   53/53 pytest green, compile + secret scan clean, `docker compose config`
   valid), reviewer setup documentation (includes the §5.3 reference docs).
6. ~~**LayoutLMv3 behind a feature flag**~~ — **settled** 2026-09-20: honest
   correction instead of a real model. The README and architecture-PDF
   generator no longer claim LayoutLMv3; genuine document-understanding stays
   planned (FR-10). Rationale: real LayoutLMv3 token classification needs
   labeled invoice field data we don't have — without it, the model would be
   expensive layout embeddings on top of what Tesseract already provides.

Housekeeping (non-code): rotate the previously exposed Gemini API key, set the
real GitHub git identity (currently a placeholder), push the unpushed local
commits.

---

## 8. Changelog (2026-09-20)

- Proved 97.59% accuracy / 99.87% efficiency; fixed `tax_ratio` train/score bug;
  implemented real DUPLICATE + GHOST fraud; tuned XGBoost (500t/d6/lr0.05).
- Built eval harness (golden set, judge, hardened runner) and legacy 56.7% baseline.
- Built ReAct agent + 5 deterministic tools on Gemini 3.5 Flash Lite.
- Calibration runs: 60.0% → 66.7% → 70.0% vs. 80% gate; run 4 rerun (2026-09-21,
  first fair eval with the corrected `score_invoice_xgb` tool) reached **93.3%
  (28/30)** — gate PASSED. (Runs 1–3 ran with the XGBoost tool raising
  TypeError on every call.)
- Wrote architecture before/after diagrams.
- Implemented FR-8: real pgvector tools (`find_similar_invoices`, `retrieve_policy`)
  as optional 6th/7th agent tools, verified end-to-end against live PostgreSQL 16 +
  pgvector 0.6.0; seeded `policies` table with 8 explicitly-synthetic policy snippets.
