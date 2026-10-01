# Procure AI - Agentic Invoice Auditor & Compliance Platform

An agentic invoice-auditing system that combines a tuned XGBoost anomaly
classifier with a LangGraph ReAct LLM auditor over deterministic verification tools.
Every headline number below is measured by a rerunnable script in this repo.

## Results

| Metric | Measured | How |
|---|---|---|
| Anomaly-classification accuracy | **97.59%** (0.88 ROC AUC), held-out 20% split of 250,000 invoices | `proofs/prove_accuracy.py` |
| Audit-prep time reduction | **99.87%** vs a 4-minute-per-invoice manual baseline | `proofs/prove_efficiency.py` |
| Agentic audit verdict accuracy | **93.3%** (28/30, 95% Wilson CI 78.7%–98.2%) on the 30-invoice golden set, graded by a live LLM judge | `evals/agent_report_llmjudge_r3.md` |
| Agentic audit findings recall | **0.867** mean, 0 agent errors | `evals/agent_report_llmjudge_r3.md` |
| Test suite | **53 tests**, all green, no live API or database | `pytest tests/` |

The agent eval ran live against Gemini `gemini-3.5-flash-lite` (free tier)
as a LangGraph `StateGraph` (`reason` → `act` → `finalize`), graded by a live
LLM judge (`JUDGE_MODE=gemini`). The two misses were a `NORMAL` false positive
(`INV-3003`, thin vendor history) and a `SPLIT_PO` miss (`INV-3015`). See
`evals/agent_report_llmjudge_r3.md` for the full ledger and
`docs/PROJECT_DOCUMENTATION.md` §4.3 for the run history.

## Features

- **Hybrid anomaly detection** — XGBoost (500 trees, depth 6, lr 0.05) on
  engineered invoice features: PO-match ratios, price variance, threshold
  proximity, tax-ratio consistency.
- **LangGraph ReAct agent auditor** — a Gemini-powered `StateGraph` with
  `reason` → `act` → `finalize` nodes over 5 deterministic
  tools (`score_invoice_xgb`, `verify_arithmetic`, `find_duplicates`,
  `check_po`, `assess_vendor`) plus 2 optional pgvector retrieval tools
  (`find_similar_invoices`, `retrieve_policy`), emitting a structured
  `APPROVE` / `FLAG` / `REJECT` audit brief with findings and evidence.
- **Six fraud classes** — `CALC_DISCREPANCY`, `DUPLICATE`, `GHOST`,
  `NORMAL`, `PRICE_DRIFT`, `SPLIT_PO`, all implemented for real in the
  data synthesizer and the agent's toolbelt.
- **Semantic retrieval** — PostgreSQL 16 + pgvector, 384-d MiniLM
  embeddings for invoice history and an 8-snippet synthetic policy corpus,
  so verdicts can cite policy sections. Verified against a live database;
  degrades gracefully when the DB is unreachable.
- **Honest serving** — `POST /audit` without a `GEMINI_API_KEY` returns a
  503, never a fabricated verdict. Per-audit tracing records wall latency,
  LLM/tool call counts, tokens (honest nulls when the backend reports none),
  and free-tier cost.
- **Engineering rigor** — 53-test pytest suite, GitHub Actions CI
  (compile → secret scan → tests), and a Docker Compose reviewer stack
  with least-privilege Postgres first boot.

## Architecture

```
PDF / scanned receipt
        │  Tesseract OCR (document_ai.py)
        ▼
raw text + 384-d MiniLM embedding ──► PostgreSQL 16 + pgvector
        │
        ▼
feature engineering ──► XGBoost risk score (anomaly_engine.py)
        │  score > 0.80
        ▼
LangGraph ReAct agent (Gemini) ──► 5 deterministic tools + 2 retrieval tools
        │
        ▼
structured audit brief ──► FastAPI (/audit) ──► Streamlit dashboard
```

Document understanding is Tesseract OCR + embeddings; LayoutLMv3 token
classification is planned future work (tracked in
`docs/PROJECT_DOCUMENTATION.md` FR-10) and is not wired in.

## Repository structure

```
├── api/                 FastAPI app (/health, /metrics, /invoices, /audit)
├── core/                anomaly_engine.py, document_ai.py, legacy auditor
│   └── agent/           LangGraph ReAct auditor, 5 deterministic tools, 2 retrieval
│                        tools, free-tier LLM throttling
├── database/            SQLAlchemy models, DDL, pgvector indexes
├── dashboard/           Streamlit executive dashboard
├── docker/              Postgres first-boot hook (least-privilege role setup)
├── docs/                full project documentation
├── evals/               golden set (30 invoices), judges, eval runners, reports
├── policies/            8 synthetic policy snippets (retrieval corpus)
├── proofs/              rerunnable accuracy + efficiency proofs
├── scripts/             data synthesis, seeding, demos, secret-scan CI script
├── tests/               53-test pytest suite
├── Dockerfile
├── docker-compose.yml   one-command reviewer stack
└── requirements*.txt
```

## Quickstart

### Option A — Docker Compose (reviewer stack)

```bash
git clone https://github.com/Manthan-hub/procure-ai-agentic-invoice-auditor-compliance-platform.git
cd procure-ai-agentic-invoice-auditor-compliance-platform
docker compose up --build
```

This brings up Postgres 16 + pgvector (least-privilege first boot via
`docker/db-init/01-procureai.sh`), a one-shot init/seed service (tables,
IVFFlat indexes, 8 embedded policy snippets, plus 500 synthetic `SAMPLE-`
invoices with real MiniLM embeddings so the dashboard opens with live
data — set `SEED_SAMPLE_INVOICES=0` to skip), the FastAPI API on
`http://localhost:8000`, and the Streamlit dashboard on
`http://localhost:8501`. For live Gemini audits, export your key first:

```bash
export GEMINI_API_KEY=your_key_here   # free-tier key from https://aistudio.google.com
docker compose up --build
```

Without a key, `/metrics`, `/invoices`, and the mock-LLM paths work;
`POST /audit` returns an honest 503.

### Option B — local virtualenv

```bash
git clone https://github.com/Manthan-hub/procure-ai-agentic-invoice-auditor-compliance-platform.git
cd procure-ai-agentic-invoice-auditor-compliance-platform
python3 -m venv venv && source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# configure (see .env.example)
cp .env.example .env   # then fill in GEMINI_API_KEY

# database (PostgreSQL 16 + pgvector required)
createdb procureai_db
export PYTHONPATH=.
python3 database/db.py          # schema + indexes
python3 scripts/seed_policies.py

# run
uvicorn api.main:app --host 127.0.0.1 --port 8000
streamlit run dashboard/app.py   # separate terminal
```

To generate the full 250K-invoice dataset (streams in 5K batches):

```bash
python3 scripts/generate_data.py
```

## Demo

`demo/procureai-demo.mp4` is a screen recording of the real system: the
FastAPI API and Streamlit dashboard started with the commands above, then two
live audits through `POST /audit` — a ghost-vendor invoice the LangGraph ReAct
agent REJECTs (unregistered vendor, unverified tax ID, decisive per the
calibrated policy) and a clean invoice from an approved vendor it APPROVEs.
Every terminal byte in the video is captured output from the running services,
not a mockup.

`demo/procureai-dashboard-demo.mp4` is the visual walkthrough: `docker compose
up --build`, the dashboard opening live on the 500-invoice sample seed, the
Invoice Inspector, a flagged invoice's agent findings, and a live `POST /audit`
verdict — no narration, just the product in action.
▶️ Watch: [demo/procureai-dashboard-demo.mp4](https://drive.google.com/file/d/1wFnb3DgTOBR4KvhnmD7by_9zfyhNkhzC/view?usp=sharing)

Architecture diagrams:

![Technology stack](docs/architecture-tech-stack.png)
![Invoice flow through the system](docs/architecture-project-flow.png)

To reproduce the same run against the Compose stack:

```bash
export GEMINI_API_KEY=your_key_here
docker compose up --build
# scenario 1: ghost vendor -> REJECT
curl -s -X POST localhost:8000/audit -H 'Content-Type: application/json' -d '{
  "invoice": {"invoice_id":"INV-DEMO-901","vendor_id":"V-GHOST-77","po_id":"PO-5510",
              "subtotal":45000.0,"tax_amount":3750.0,"total_amount":48750.0,
              "invoice_date":"2026-09-28",
              "line_items":[{"description":"Industrial valves","quantity":150,
                             "unit_price":300.0,"line_total":45000.0,"po_unit_price":295.0}]},
  "po": {"po_id":"PO-5510","amount_limit":50000.0},
  "vendor": {"vendor_id":"V-GHOST-77","vendor_name":"QuickSupply Trading Co.",
             "risk_rating":0.85,"on_file":false,"tax_id":"UNVERIFIED"},
  "history": []}'
# scenario 2: approved vendor, PO-aligned pricing -> APPROVE
curl -s -X POST localhost:8000/audit -H 'Content-Type: application/json' -d '{
  "invoice": {"invoice_id":"INV-DEMO-902","vendor_id":"V-ACME-014","po_id":"PO-5520",
              "subtotal":4800.0,"tax_amount":384.0,"total_amount":5184.0,
              "invoice_date":"2026-09-29",
              "line_items":[{"description":"Office supplies","quantity":48,
                             "unit_price":100.0,"line_total":4800.0,"po_unit_price":100.0}]},
  "po": {"po_id":"PO-5520","amount_limit":15000.0},
  "vendor": {"vendor_id":"V-ACME-014","vendor_name":"Acme Industrial Ltd.",
             "risk_rating":0.12,"on_file":true,"tax_id":"TAX-88412-CA"},
  "history": []}'
```

Then open `http://localhost:8501` — the same dashboard: KPI cards, invoice
inspector, and the on-demand agentic audit (the same agent, one click).

## Configuration

All secrets come from the environment — nothing is committed. Copy
`.env.example` to `.env` and fill in values.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | *(required for live audits)* | Google AI Studio key (free tier) |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | LLM model; restricted to the free-tier allowlist in `core/agent/llm_throttle.py` |
| `DATABASE_URL` | `postgresql+psycopg2://procureai:procureai@localhost:5432/procureai_db` | SQLAlchemy connection string |
| `APP_DB_USER` / `APP_DB_PASSWORD` / `APP_DB_NAME` | `procureai` / `procureai` / `procureai_db` | Compose app-role credentials (override for anything beyond a local demo) |
| `PROCUREAI_API_URL` | `http://localhost:8000` | Dashboard → API base URL |
| `AGENT_MAX_STEPS` | `8` | LangGraph ReAct graph step cap |
| `GEMINI_FREE_TIER_RPM` | `15` | Client-side rate limit for the free tier |

## API reference

| Method | Path | Description |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/metrics` | dataset/audit counters |
| GET | `/invoices` | invoice listing |
| POST | `/audit` | full agentic audit (verdict, findings, evidence, trace, degraded state) |
| POST | `/audit/run` | audit pipeline entrypoint |

Every `POST /audit` response carries a `trace` object: wall latency,
LLM/tool call counts, token usage, and cost (0.0 on the free tier).

## Evaluation

Agent evals use a 30-invoice golden set (`evals/golden_invoices.json`,
5 per fraud class) with a mock judge (deterministic, default) or a live LLM
judge (`JUDGE_MODE=gemini`):

```bash
# fast, deterministic: scripted LLM + mock judge (no API key needed)
python3 evals/run_agent_evals.py --mock-llm

# live: real Gemini agent, mock judge
export GEMINI_API_KEY=your_key_here
python3 evals/run_agent_evals.py

# live agent, graded by the LLM judge
JUDGE_MODE=gemini python3 evals/run_agent_evals.py \
  --results evals/agent_results_llmjudge.json \
  --report evals/agent_report_llmjudge.md
```

Reports land in `evals/agent_report*.md` / `evals/agent_results*.json`.
Known limitations are documented, not hidden: the persistent `NORMAL` false
positive on thin vendor history (`INV-3003`), weaker duplicate findings
recall, and evaluator-label leakage in the duplicate-history fixture
construction (`docs/PROJECT_DOCUMENTATION.md` §4.3) — the 93.3% stands as
the measured result of the live LLM-judged run, not a final number.

Accuracy and efficiency proofs:

```bash
python3 proofs/prove_accuracy.py    # 97.59% accuracy / 0.88 ROC AUC claim
python3 proofs/prove_efficiency.py  # 99.87% time-reduction claim
```

Efficiency methodology: automated triage wall-clock extrapolated to 250K
invoices vs a documented 4-minute-per-invoice manual baseline
(parameter `--manual-min`; an industry assumption, not a measurement).

## Testing

```bash
python -m pytest tests/ -q          # 53 tests, no live API or database
python -m compileall -q api core tests scripts evals proofs
bash scripts/ci_secret_scan.sh      # fails on committed key patterns
```

CI runs all three on every push/PR.

## Roadmap

- Calibrate the agent to cut the two `NORMAL` false positives without
  harming fraud recall.
- Rebuild duplicate-history fixtures without evaluator-label leakage and
  rerun the eval as the final number.
- Decide whether pgvector retrieval should be mandatory in the agent loop
  (currently optional; costs extra LLM steps/quota per invoice).
- Re-embed the full 250K invoice corpus with real embeddings (stored
  embeddings are placeholders; verification used a real 3K subset).
- LayoutLMv3 document understanding behind an off-by-default feature flag.
- Reviewer setup documentation.

See `docs/PROJECT_DOCUMENTATION.md` §7 for the full sequenced roadmap and
§8 for the changelog.

## License

MIT — see [LICENSE](LICENSE).
