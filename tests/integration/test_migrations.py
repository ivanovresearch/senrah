"""
tests/integration/test_migrations.py — verify the Alembic migration.

Covers STORE-01 / STORE-02 / STORE-03 at the schema level:
- vector extension installed
- All four tables exist (projects, repositories, pull_requests, skills)
- pull_requests has diff and files_changed columns (STORE-02)
- skills has two HNSW indexes using vector_cosine_ops (STORE-03 guard)
"""

from __future__ import annotations

import psycopg
import pytest
from pgvector.psycopg import register_vector


@pytest.fixture(scope="module")
def conn(pg_dsn_migrated: str):
    """Open a psycopg3 connection to the migrated test DB."""
    with psycopg.connect(pg_dsn_migrated) as connection:
        register_vector(connection)
        yield connection


def _table_exists(conn, table_name: str) -> bool:
    """Return True if table_name exists in the public schema."""
    row = conn.execute(
        "SELECT to_regclass(%s::text)",
        (f"public.{table_name}",),
    ).fetchone()
    return row is not None and row[0] is not None


def _extension_installed(conn, ext_name: str) -> bool:
    """Return True if the PostgreSQL extension is installed."""
    row = conn.execute(
        "SELECT 1 FROM pg_extension WHERE extname = %s",
        (ext_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Return True if column_name exists in table_name (public schema)."""
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = %s
          AND column_name  = %s
        """,
        (table_name, column_name),
    ).fetchone()
    return row is not None


def _hnsw_indexes_for_table(conn, table_name: str) -> list[dict]:
    """Return HNSW index records (indexname, indexdef) for the given table."""
    rows = conn.execute(
        """
        SELECT indexname, indexdef
        FROM   pg_indexes
        WHERE  tablename = %s
          AND  indexdef ILIKE '%%hnsw%%'
        """,
        (table_name,),
    ).fetchall()
    return [{"name": r[0], "def": r[1]} for r in rows]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_vector_extension_installed(conn):
    """CREATE EXTENSION vector must be present after migration."""
    assert _extension_installed(conn, "vector"), (
        "pgvector extension is not installed. Check that the migration starts with "
        "CREATE EXTENSION IF NOT EXISTS vector and that the Docker image is "
        "pgvector/pgvector:pg17 (not plain postgres)."
    )


def test_projects_table_exists(conn):
    assert _table_exists(conn, "projects"), "Table 'projects' does not exist after migration"


def test_repositories_table_exists(conn):
    assert _table_exists(conn, "repositories"), "Table 'repositories' does not exist"


def test_pull_requests_table_exists(conn):
    assert _table_exists(conn, "pull_requests"), "Table 'pull_requests' does not exist"


def test_skills_table_exists(conn):
    assert _table_exists(conn, "skills"), "Table 'skills' does not exist"


def test_pull_requests_has_diff_column(conn):
    """STORE-02: pull_requests must have a 'diff' column for raw diff storage."""
    assert _column_exists(conn, "pull_requests", "diff"), (
        "Column 'diff' missing from pull_requests. Required by STORE-02."
    )


def test_pull_requests_has_files_changed_column(conn):
    """STORE-02 / Pitfall 5: pull_requests must have 'files_changed' (JSONB)."""
    assert _column_exists(conn, "pull_requests", "files_changed"), (
        "Column 'files_changed' missing from pull_requests. Required by STORE-02 / Pitfall 5."
    )


def test_skills_has_two_hnsw_indexes(conn):
    """STORE-03: skills table must have exactly two HNSW indexes."""
    indexes = _hnsw_indexes_for_table(conn, "skills")
    assert len(indexes) == 2, (
        f"Expected 2 HNSW indexes on skills, found {len(indexes)}: "
        f"{[i['name'] for i in indexes]}"
    )


def test_hnsw_indexes_use_vector_cosine_ops(conn):
    """STORE-03 / Pitfall 1: both HNSW indexes must use vector_cosine_ops.

    The operator class MUST match the <=> query operator or PostgreSQL will
    silently fall back to a full sequential scan (no error, just O(N) queries).
    """
    indexes = _hnsw_indexes_for_table(conn, "skills")
    for idx in indexes:
        assert "vector_cosine_ops" in idx["def"], (
            f"HNSW index '{idx['name']}' does not use vector_cosine_ops. "
            f"Index definition: {idx['def']}. "
            "This MUST match the <=> operator used in queries (Pitfall 1)."
        )


def test_hnsw_index_names(conn):
    """Verify the expected index names exist."""
    indexes = _hnsw_indexes_for_table(conn, "skills")
    names = {idx["name"] for idx in indexes}
    assert "idx_skills_problem_emb" in names, (
        f"Expected index 'idx_skills_problem_emb' not found. Indexes: {names}"
    )
    assert "idx_skills_solution_emb" in names, (
        f"Expected index 'idx_skills_solution_emb' not found. Indexes: {names}"
    )
