"""
tests/integration/test_skill_repo_window.py -- Integration test stubs for DEPTH-02 SQL window filter.

Tests that SkillRepo.search correctly filters results by merged_at when merged_before
and merged_after parameters are supplied.

These are WAVE-0 stubs; the merged_before/merged_after params are added to
SkillRepo.search in Plan 02. All tests are xfail(strict=False) so they skip
gracefully until the implementation exists.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

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


# ---------------------------------------------------------------------------
# Helpers (mirror test_skill_repo.py exactly)
# ---------------------------------------------------------------------------


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


def _insert_pull_request(
    conn: psycopg.Connection,
    repo_id: int,
    number: int,
    merged_at: Optional[datetime] = None,
) -> int:
    """Insert a minimal pull_request row (with optional merged_at) and return its id."""
    row = conn.execute(
        """
        INSERT INTO pull_requests (repository_id, number, title, merged_at)
        VALUES (%(repo_id)s, %(number)s, %(title)s, %(merged_at)s)
        ON CONFLICT (repository_id, number) DO UPDATE
            SET title = EXCLUDED.title,
                merged_at = EXCLUDED.merged_at
        RETURNING id
        """,
        {
            "repo_id": repo_id,
            "number": number,
            "title": f"PR #{number}",
            "merged_at": merged_at,
        },
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
# Tests: window ceiling (merged_before)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="DEPTH-02 not implemented -- merged_before param does not exist yet",
)
async def test_search_window_ceiling(db_conn, fake_embedder):
    """Insert two PRs around cutoff T; search with merged_before=T; only the pre-T PR appears."""
    pytest.xfail("DEPTH-02 not implemented")


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="DEPTH-02 not implemented -- merged_after param does not exist yet",
)
async def test_search_window_floor(db_conn, fake_embedder):
    """Insert PRs at T-400days and T-100days; search with merged_after=T-200days; only the T-100days PR appears."""
    pytest.xfail("DEPTH-02 not implemented")


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="DEPTH-02 not implemented -- marks intent for backward-compat assertion",
)
async def test_search_window_both_none(db_conn, fake_embedder):
    """With both merged_before and merged_after omitted, existing search behavior is unchanged."""
    pytest.xfail("DEPTH-02 not implemented")
