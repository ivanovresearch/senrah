"""
tests/integration/test_skill_repo.py — HNSW Index Scan proof (STORE-03).

Inserts fake-embedding skill rows, then runs EXPLAIN (FORMAT JSON) on a cosine
similarity query.  Asserts the plan shows Index Scan (not Seq Scan), confirming
the HNSW index and vector_cosine_ops operator class are wired correctly.

This guards against Pitfall 1: a <-> (L2) operator against a vector_cosine_ops
index silently falls back to a full sequential scan.
"""

from __future__ import annotations

import json

import psycopg
import pytest
from pgvector.psycopg import register_vector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_conn(pg_dsn_migrated: str):
    """Open a module-scoped psycopg3 connection to the migrated test DB."""
    with psycopg.connect(pg_dsn_migrated) as conn:
        register_vector(conn)
        yield conn


def _insert_project(conn: psycopg.Connection, name: str) -> int:
    """Insert a project row and return its id."""
    row = conn.execute(
        "INSERT INTO projects (name) VALUES (%(name)s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        {"name": name},
    ).fetchone()
    conn.commit()
    return row[0]


def _insert_repository(conn: psycopg.Connection, project_id: int, name: str) -> int:
    """Insert a repository row and return its id."""
    row = conn.execute(
        """
        INSERT INTO repositories (project_id, type, name)
        VALUES (%(project_id)s, 'github', %(name)s)
        ON CONFLICT (project_id, name) DO UPDATE SET type = EXCLUDED.type
        RETURNING id
        """,
        {"project_id": project_id, "name": name},
    ).fetchone()
    conn.commit()
    return row[0]


def _insert_pull_request(conn: psycopg.Connection, repo_id: int, number: int) -> int:
    """Insert a minimal pull_request row and return its id."""
    row = conn.execute(
        """
        INSERT INTO pull_requests (repository_id, number, title)
        VALUES (%(repo_id)s, %(number)s, %(title)s)
        ON CONFLICT (repository_id, number) DO UPDATE SET title = EXCLUDED.title
        RETURNING id
        """,
        {"repo_id": repo_id, "number": number, "title": f"PR #{number}"},
    ).fetchone()
    conn.commit()
    return row[0]


def _insert_skill(
    conn: psycopg.Connection,
    pr_id: int,
    problem_vec: list[float],
    solution_vec: list[float],
) -> None:
    """Insert a skill row with the given embedding vectors."""
    conn.execute(
        """
        INSERT INTO skills
            (pr_id, problem_embedding, solution_embedding, embedding_model, embedding_version)
        VALUES
            (%(pr_id)s, %(problem)s::vector, %(solution)s::vector, 'text-embedding-3-small', 'v1')
        ON CONFLICT (pr_id, embedding_model, embedding_version) DO NOTHING
        """,
        {
            "pr_id": pr_id,
            "problem": problem_vec,
            "solution": solution_vec,
        },
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------


def test_search_uses_hnsw_index(db_conn, fake_embedder):
    """STORE-03: EXPLAIN on a <=> query must show Index Scan, not Seq Scan.

    Steps:
    1. Insert enough rows to trigger the HNSW index (pgvector uses HNSW once
       there are rows in the table; it works correctly even on small tables).
    2. Run EXPLAIN (FORMAT JSON) on an ORDER BY ... <=> ... LIMIT 5 query.
    3. Assert 'Index Scan' appears somewhere in the plan JSON.

    This guards against the silent operator-class mismatch (Pitfall 1):
    if the index used L2 (vector_l2_ops) but the query uses <=> (cosine),
    PostgreSQL would return a Seq Scan with no error.
    """
    # Set up: project → repository → 20 PRs → 20 skill rows
    project_id = _insert_project(db_conn, "test-hnsw-project")
    repo_id = _insert_repository(db_conn, project_id, "test-org/test-repo")

    # Insert enough rows to make the HNSW planner choose an index scan
    # (pgvector HNSW typically activates immediately; 20 rows is sufficient
    # with the default work_mem and enable_seqscan settings)
    num_rows = 20
    for i in range(num_rows):
        pr_id = _insert_pull_request(db_conn, repo_id, number=i + 1000)
        problem_vec = fake_embedder(f"problem text {i}")
        solution_vec = fake_embedder(f"solution diff {i}")
        _insert_skill(db_conn, pr_id, problem_vec, solution_vec)

    # Force the planner to prefer index scans (disable seq scan for this session)
    db_conn.execute("SET enable_seqscan = off")

    # Build a query vector for the EXPLAIN probe
    query_vec = fake_embedder("search for similar problems")

    # Run EXPLAIN (FORMAT JSON) to inspect the query plan
    row = db_conn.execute(
        """
        EXPLAIN (FORMAT JSON)
        SELECT 1 FROM skills
        ORDER BY problem_embedding <=> %(vec)s::vector
        LIMIT 5
        """,
        {"vec": query_vec},
    ).fetchone()

    # Re-enable seq scan (clean up session state)
    db_conn.execute("SET enable_seqscan = on")

    assert row is not None, "EXPLAIN returned no rows"
    plan = row[0]  # psycopg3 returns JSONB as a Python object

    # Convert plan to string for easy substring search
    plan_text = json.dumps(plan) if not isinstance(plan, str) else plan

    assert "Index Scan" in plan_text, (
        "Expected HNSW Index Scan in query plan but got Seq Scan.\n"
        "This indicates a vector_cosine_ops / <=> operator class mismatch (Pitfall 1).\n"
        f"Query plan:\n{plan_text}"
    )

    # Extra sanity check: confirm no Seq Scan in the plan
    assert "Seq Scan" not in plan_text, (
        "Query plan contains Seq Scan — HNSW index is not being used.\n"
        f"Plan: {plan_text}"
    )
