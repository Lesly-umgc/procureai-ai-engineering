"""Smoke test for the pgvector retrieval tools (no live DB required).

Verifies everything that can be verified without a Postgres+pgvector server:
  1. retrieval + tools modules import cleanly; 7 tools registered.
  2. Both tools degrade gracefully (available=false, structured error) when
     the DB or the embedding dependency is missing -- they never raise.
  3. policies/policies.json parses and has the required schema.
  4. The pgvector SQL statements compile against the Postgres dialect
     (query construction is valid without a server).
  5. The agent's _dispatch routes the new tools through the default
     single-invoice branch without crashing the ReAct loop.

A full live verification (real similarity ranking + policy retrieval)
requires: Postgres with the pgvector extension, DATABASE_URL set, the
policies table seeded via scripts/seed_policies.py, and
sentence-transformers installed. That part is explicitly UNVERIFIED here.

Run: cd ~/workspace/procureai && .venv/bin/python scripts/smoke_retrieval.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.agent.tools as tools
from core.agent import retrieval
from core.agent.retrieval import find_similar_invoices, retrieve_policy, _invoice_query_text

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "evals", "golden_invoices.json")
SEED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "policies", "policies.json")

checks = []


def check(name, fn):
    try:
        fn()
        checks.append((name, True, ""))
        print(f"  PASS {name}")
    except AssertionError as e:
        checks.append((name, False, str(e)))
        print(f"  FAIL {name}: {e}")


def t_registry():
    assert len(tools.TOOLS) == 7, f"expected 7 tools, got {len(tools.TOOLS)}"
    assert "find_similar_invoices" in tools.TOOLS
    assert "retrieve_policy" in tools.TOOLS
    desc = tools.tool_descriptions()
    assert "pgvector" in desc


def t_query_text():
    inv = {"invoice_id": "INV-3001", "vendor_id": "V-7", "po_id": "PO-1",
           "subtotal": 100.0, "tax_amount": 8.0, "total_amount": 108.0,
           "line_items": [{"description": "widgets", "line_total": 100.0}]}
    txt = _invoice_query_text(inv)
    assert "INV-3001" in txt and "widgets" in txt
    inv2 = dict(inv, raw_text="ACME invoice total 108")
    assert _invoice_query_text(inv2) == "ACME invoice total 108"


def t_graceful_degrade():
    with open(GOLDEN_PATH) as f:
        golden = json.load(f)
    inv = golden["invoices"][0]  # golden records are invoice dicts + eval fields
    for fn in (find_similar_invoices, retrieve_policy):
        out = fn(inv)
        assert isinstance(out, dict), f"{fn.__name__} did not return a dict"
        assert out.get("available") is False, f"{fn.__name__} should degrade, got {out}"
        assert "error" in out and out["error"], f"{fn.__name__} missing error detail"
    print(f"     (degrade reasons: {find_similar_invoices(inv)['error'][:80]}...)")


def t_policy_seed():
    with open(SEED_PATH) as f:
        seed = json.load(f)
    assert len(seed["policies"]) == 8, f"expected 8 policies, got {len(seed['policies'])}"
    for p in seed["policies"]:
        for field in ("section", "title", "text", "fraud_classes"):
            assert p.get(field), f"policy missing {field}: {p.get('section')}"
        assert isinstance(p["fraud_classes"], list)
    classes = {c for p in seed["policies"] for c in p["fraud_classes"]}
    for fc in ("DUPLICATE", "SPLIT_PO", "PRICE_DRIFT", "GHOST", "CALC_DISCREPANCY", "NORMAL"):
        assert fc in classes, f"fraud class {fc} not covered by seed corpus"


def t_sql_compiles():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy import select
    from database.db import Invoice, Policy
    vec = [0.0] * 384
    s1 = (select(Invoice.invoice_number,
                Invoice.embedding.cosine_distance(vec).label("distance"))
          .order_by("distance").limit(5))
    s2 = (select(Policy.section, Policy.title,
                Policy.embedding.cosine_distance(vec).label("distance"))
          .order_by("distance").limit(3))
    for s in (s1, s2):
        sql = str(s.compile(dialect=postgresql.dialect(),
                            compile_kwargs={"literal_binds": True}))
        assert "<=>" in sql, f"pgvector distance operator missing: {sql[:120]}"


def t_agent_dispatch():
    from core.agent.agentic_auditor import AuditAgent
    agent = AuditAgent(llm_fn=lambda prompt: '{"verdict":"APPROVE"}')
    inv = {"invoice_id": "INV-X", "total_amount": 10.0}
    ctx = {"invoice": inv, "po": {}, "vendor": {}, "history": []}
    for name in ("find_similar_invoices", "retrieve_policy"):
        out = agent._dispatch(name, {}, ctx)
        assert isinstance(out, dict) and out.get("available") is False, \
            f"dispatch({name}) should degrade gracefully, got {out}"


print("smoke_retrieval: verifying pgvector tools (no live DB in this environment)")
check("tool registry has 7 tools incl. pgvector pair", t_registry)
check("invoice query-text builder", t_query_text)
check("graceful degradation without DB/model", t_graceful_degrade)
check("policy seed JSON schema + class coverage", t_policy_seed)
check("pgvector SQL compiles (postgres dialect)", t_sql_compiles)
check("agent _dispatch routes new tools safely", t_agent_dispatch)

failed = [n for n, ok, _ in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
if failed:
    sys.exit(1)
print("UNVERIFIED (needs live Postgres+pgvector): real similarity ranking, "
      "policy retrieval hits, seed_policies.py end-to-end.")
