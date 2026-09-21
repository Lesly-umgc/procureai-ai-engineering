"""Seed the pgvector `policies` table from policies/policies.json.

Usage:
    cd ~/workspace/procureai && .venv/bin/python scripts/seed_policies.py

Needs: DATABASE_URL pointing at a Postgres with the pgvector extension,
and the sentence-transformers dependency (all-MiniLM-L6-v2 weights) so
policy texts get real embeddings via core/document_ai.py.

The corpus is SYNTHETIC seed data (see policies/policies.json) — it exists
so the audit agent can cite policy sections; it is not a real company policy.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine

from database.db import Base, Policy

SEED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "policies", "policies.json")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://@localhost:5432/procureai_db")


def _get_encoder():
    try:
        from core.document_ai import DocumentAIProcessor
    except Exception as e:  # noqa: BLE001 - sentence_transformers missing
        raise SystemExit(
            "seed_policies.py needs the embedding dependency: "
            "pip install sentence-transformers (and network access to download "
            f"the all-MiniLM-L6-v2 weights). Underlying error: {e}"
        ) from e
    return DocumentAIProcessor()


def main() -> None:
    with open(SEED_PATH) as f:
        seed = json.load(f)
    policies = seed["policies"]
    print(f"Embedding {len(policies)} synthetic policy snippets "
          f"(all-MiniLM-L6-v2, 384-dim, normalized)...")
    encoder = _get_encoder()

    engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 10})
    Base.metadata.create_all(engine, tables=[Policy.__table__])
    with engine.connect() as conn:
        conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector;")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS policies_embedding_idx ON policies "
            "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);"
        )
        conn.commit()

    from sqlalchemy.orm import Session
    inserted = 0
    with Session(engine) as session:
        for p in policies:
            exists = session.query(Policy).filter_by(section=p["section"]).first()
            vec = encoder.generate_embedding(f"{p['title']}. {p['text']}")
            if exists:
                exists.title = p["title"]
                exists.text = p["text"]
                exists.fraud_classes = ",".join(p["fraud_classes"])
                exists.embedding = vec
            else:
                session.add(Policy(
                    section=p["section"],
                    title=p["title"],
                    text=p["text"],
                    fraud_classes=",".join(p["fraud_classes"]),
                    embedding=vec,
                ))
                inserted += 1
        session.commit()
    print(f"Done: {inserted} new policies inserted, "
          f"{len(policies) - inserted} updated. Table: policies.")


if __name__ == "__main__":
    main()
