# ProcureAI: Before → After

How the repo worked when we found it, and the industry-level target we're building toward.

---

## 1. BEFORE — the repo as it was

```mermaid
flowchart TD
    subgraph DATA["📦 Data layer"]
        GEN["scripts/generate_data.py<br/>250K synthetic invoices<br/>streams to DB in batches"]
        GEN --> PG[("PostgreSQL<br/>invoices / pos / vendors")]
        PG --> VEC[("pgvector<br/>⚠️ indexes created<br/>but NEVER queried")]
    end

    subgraph REQ["🔍 One audit request"]
        UI["Streamlit UI"]
        UI --> API["FastAPI  POST /audit"]
        API --> XGB["AnomalyScoringEngine<br/>XGBoost → anomaly score<br/>⚠️ train/score computed<br/>tax_ratio differently"]
        API --> AG["AgentAuditor.audit_invoice()<br/>builds ONE prompt<br/>→ ONE Gemini call<br/>→ parses JSON<br/>⚠️ not an agent: no tools,<br/>no loop, no evidence gathering"]
        AG --> VERD["verdict: APPROVE / FLAG / REJECT"]
    end

    subgraph CLAIM["🏷️ README claims (unproven)"]
        C1["❌ 96% anomaly accuracy<br/>no eval, no report"]
        C2["❌ 78.5% audit-time cut<br/>hardcoded constant in api/main.py"]
        C3["❌ LayoutLMv3 document AI<br/>actually Tesseract + embeddings"]
        C4["❌ 'agentic auditing'<br/>single LLM call"]
        C5["❌ 250K invoices at scale<br/>generates, but nothing<br/>validates the pipeline on it"]
    end

    subgraph MISS["🕳️ Missing entirely"]
        M1["no tests"]
        M2["no CI"]
        M3["no Docker / reproducible setup"]
        M4["no tracing, latency or cost tracking"]
        M5["no eval dataset"]
    end
```

**In one sentence:** a demo-shaped pipeline — real components (Postgres, XGBoost, FastAPI, Streamlit) wired together, but the "agent" was a single prompt, the metrics were assertions, and nothing measured anything.

---

## 2. AFTER — the AI-engineer industry-level target

```mermaid
flowchart TD
    subgraph DATA2["📦 Data layer — proven"]
        SYN["scripts/synthesize.py<br/><b>single source of truth</b><br/>DB loader, proofs & evals<br/>all draw from here"]
        SYN --> PG2[("PostgreSQL + pgvector<br/>✅ REAL similarity search:<br/>find_similar_invoices()<br/>policy retrieval")]
        SYN --> PROOF["proofs/prove_accuracy.py<br/>✅ 97.59% on 250K held-out<br/>proofs/prove_efficiency.py<br/>✅ 99.87% time reduction"]
    end

    subgraph INTEL["🧠 Intelligence layer"]
        XGB2["XGBoost<br/>500 trees · tuned · proven<br/>catches split-PO, price drift,<br/>calc discrepancies"]
        RULES["Deterministic rules<br/>— arithmetic MUST balance<br/>— duplicate detection<br/>— PO-limit enforcement<br/>— ghost-vendor signals<br/><i>rules for rules, ML for patterns</i>"]
    end

    subgraph AGENT2["🤖 Agentic layer — real ReAct loop"]
        AA["AuditAgent.audit()<br/><b>reason → act → observe → repeat</b><br/>free-tier Gemini"]
        AA --> T1["🔧 verify_arithmetic<br/><i>certain, runs first</i>"]
        AA --> T2["🔧 find_duplicates"]
        AA --> T3["🔧 check_po"]
        AA --> T4["🔧 assess_vendor"]
        AA --> T5["🔧 score_invoice_xgb"]
        AA --> T6["🔧 policy retrieval<br/>(pgvector)"]
        T1 & T2 & T3 & T4 & T5 & T6 --> EV["evidence-backed verdict<br/>APPROVE / FLAG / REJECT<br/>+ cited findings"]
    end

    subgraph EVAL2["📏 Eval layer — the gatekeeper"]
        GOLD["evals/golden_invoices.json<br/>30 invoices · 5 per fraud type<br/>known-correct verdicts"]
        JUDGE["LLM-as-judge<br/>verdict match + findings recall"]
        GOLD --> RUN["run_agent_evals.py"]
        RUN --> JUDGE
        JUDGE --> GATE{"CI gate"}
        GATE -->|"pass"| SHIP["✅ merge & deploy"]
        GATE -->|"fail"| BLOCK["🛑 block merge"]
    end

    subgraph SERVE["🚀 Serving & ops"]
        API2["FastAPI<br/>tracing · latency ·<br/>cost per audit"]
        UI2["Streamlit UI"]
        DOCK["Docker Compose<br/>one-command setup"]
        CI["GitHub Actions<br/>pytest + evals on PR"]
    end

    XGB2 --> AA
    RULES --> AA
    EV --> API2
    API2 --> UI2
```

---

## 3. What changed and why (the delta)

| Area | Before | After | Why it matters |
|---|---|---|---|
| "Agent" | 1 prompt → 1 LLM call | ReAct loop over 6 deterministic tools | A real agent gathers evidence; a prompt guesses |
| Metrics | hardcoded / asserted | measured by rerunnable proofs | Reviewers can re-run `proofs/` and see the numbers |
| Evals | none | 30 golden invoices + LLM judge, CI-gated | Every agent change is scored — no regressions |
| pgvector | indexes, never queried | similarity search + policy retrieval | Claim becomes a working feature |
| Rules vs ML | everything through the LLM | arithmetic/duplicates = code, patterns = XGBoost, ambiguity = agent | Cheaper, faster, more reliable; the LLM only does what needs judgment |
| Doc AI | README said LayoutLMv3, code used Tesseract | honest README correction, done 2026-09-20 (genuine model stays planned, FR-10) | No claim survives without an implementation |
| Ops | no tests/CI/Docker | pytest + GitHub Actions + Docker Compose | Anyone can clone, run, and verify |

## 4. Build order (where we are)

- [x] Prove the numbers (97.59% accuracy, 99.87% efficiency, 250K scale)
- [x] Eval harness + baseline (legacy auditor: 56.7% — honest "before")
- [x] Real ReAct agent (evaluating now)
- [x] pgvector similarity + policy retrieval wired into the agent
  - ✅ **Done and verified 2026-09-20 against a live PostgreSQL 16 + pgvector 0.6.0.**
  - New tools: `find_similar_invoices` (cosine search over real MiniLM-L6-v2
    embeddings) and `retrieve_policy` (search over 8 synthetic, clearly-labeled
    policy snippets covering all 6 fraud classes).
  - Verified end-to-end: real rows returned, distances correctly sorted, graceful
    degradation when the DB is unreachable.
  - Honest limits: verified on a 3,000-row subset; synthetic invoice text is a
    fixed template so embeddings cluster tightly (rankings need real varied text);
    retrieval is currently *optional* in the agent's mandatory sweep (decision
    pending before the next live eval, to protect API quota).
- [x] LayoutLMv3 document-AI path (or honest README correction) — settled 2026-09-20: honest correction; genuine model stays planned (FR-10)
- [ ] Tracing, latency & cost per audit
- [ ] pytest suite, GitHub Actions, Docker Compose
- [ ] Push `ai-engineer-upgrade` → review → merge
