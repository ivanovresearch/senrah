"""
tests/integration/test_skill_repo_window.py -- Integration tests for DEPTH-02 SQL window filter.

Tests that SkillRepo.search correctly filters results by merged_at when merged_before
and merged_after parameters are supplied. Uses a real pgvector container via the
session-scoped pg_dsn_migrated fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

import psycopg
import pytest
from pgvector.psycopg import register_vector, register_vector_async

from senrah.db.repos.skill import SkillRepo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_conn(pg_dsn_migrated: str):
    """Open a module-scoped psycopg3 sync connection for data setup."""
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
# Shared search helper (opens async connection per call)
# ---------------------------------------------------------------------------


async def _search(pg_dsn: str, query_vec: list[float], **kwargs) -> list:
    """Run SkillRepo.search over an async connection and return results."""
    async with await psycopg.AsyncConnection.connect(pg_dsn) as async_conn:
        await register_vector_async(async_conn)
        repo = SkillRepo(async_conn)
        return await repo.search(query_vec=query_vec, **kwargs)


# ---------------------------------------------------------------------------
# Tests: window ceiling (merged_before)
# ---------------------------------------------------------------------------


async def test_search_window_ceiling(db_conn, pg_dsn_migrated: str, fake_embedder):
    """Insert two PRs around cutoff T; search with merged_before=T; only the pre-T PR appears."""
    T = datetime(2024, 6, 1, tzinfo=timezone.utc)
    pre_T = T - timedelta(days=1)   # merged_at < T  -> should be included
    post_T = T + timedelta(days=1)  # merged_at >= T -> should be excluded

    project_id = _insert_project(db_conn, "window-ceiling-project")
    repo_id = _insert_repository(db_conn, project_id, "test-org/window-ceiling-repo")

    # Use high-similarity vectors so both PRs pass score_threshold=0.0
    # (the window filter, not the score, determines what appears)
    vec = [0.1] + [0.0] * 1535
    magnitude = sum(x * x for x in vec) ** 0.5
    unit_vec = [x / magnitude for x in vec]

    pr_pre_id = _insert_pull_request(db_conn, repo_id, number=2001, merged_at=pre_T)
    pr_post_id = _insert_pull_request(db_conn, repo_id, number=2002, merged_at=post_T)

    _insert_skill(db_conn, pr_pre_id, unit_vec, unit_vec)
    _insert_skill(db_conn, pr_post_id, unit_vec, unit_vec)

    results = await _search(
        pg_dsn_migrated,
        query_vec=unit_vec,
        top_n=5,
        oversample_factor=5,
        score_threshold=0.0,
        problem_weight=0.7,
        solution_weight=0.3,
        merged_before=T,
    )

    result_numbers = [r.number for r in results]
    assert 2001 in result_numbers, (
        f"Expected PR #2001 (pre-T) in results, got: {result_numbers}"
    )
    assert 2002 not in result_numbers, (
        f"PR #2002 (post-T) must be excluded by merged_before={T}, got: {result_numbers}"
    )


# ---------------------------------------------------------------------------
# Tests: window floor (merged_after)
# ---------------------------------------------------------------------------


async def test_search_window_floor(db_conn, pg_dsn_migrated: str, fake_embedder):
    """Insert PRs at T-400days and T-100days; search with merged_after=T-200days; only the T-100days PR appears."""
    T = datetime(2024, 12, 1, tzinfo=timezone.utc)
    old_pr_date = T - timedelta(days=400)   # merged_at < floor -> should be excluded
    new_pr_date = T - timedelta(days=100)   # merged_at >= floor -> should be included
    floor = T - timedelta(days=200)

    project_id = _insert_project(db_conn, "window-floor-project")
    repo_id = _insert_repository(db_conn, project_id, "test-org/window-floor-repo")

    vec = [0.2] + [0.0] * 1535
    magnitude = sum(x * x for x in vec) ** 0.5
    unit_vec = [x / magnitude for x in vec]

    pr_old_id = _insert_pull_request(db_conn, repo_id, number=3001, merged_at=old_pr_date)
    pr_new_id = _insert_pull_request(db_conn, repo_id, number=3002, merged_at=new_pr_date)

    _insert_skill(db_conn, pr_old_id, unit_vec, unit_vec)
    _insert_skill(db_conn, pr_new_id, unit_vec, unit_vec)

    results = await _search(
        pg_dsn_migrated,
        query_vec=unit_vec,
        top_n=5,
        oversample_factor=5,
        score_threshold=0.0,
        problem_weight=0.7,
        solution_weight=0.3,
        merged_after=floor,
    )

    result_numbers = [r.number for r in results]
    assert 3002 in result_numbers, (
        f"Expected PR #3002 (new, within floor) in results, got: {result_numbers}"
    )
    assert 3001 not in result_numbers, (
        f"PR #3001 (old, before floor) must be excluded by merged_after={floor}, got: {result_numbers}"
    )


# ---------------------------------------------------------------------------
# Tests: backward compat (both params None)
# ---------------------------------------------------------------------------


async def test_search_window_both_none(db_conn, pg_dsn_migrated: str, fake_embedder):
    """With both merged_before and merged_after omitted, existing search behavior is unchanged."""
    project_id = _insert_project(db_conn, "window-both-none-project")
    repo_id = _insert_repository(db_conn, project_id, "test-org/window-both-none-repo")

    T = datetime(2024, 3, 15, tzinfo=timezone.utc)

    vec = [0.3] + [0.0] * 1535
    magnitude = sum(x * x for x in vec) ** 0.5
    unit_vec = [x / magnitude for x in vec]

    pr1_id = _insert_pull_request(db_conn, repo_id, number=4001, merged_at=T - timedelta(days=500))
    pr2_id = _insert_pull_request(db_conn, repo_id, number=4002, merged_at=T)
    pr3_id = _insert_pull_request(db_conn, repo_id, number=4003, merged_at=T + timedelta(days=200))

    _insert_skill(db_conn, pr1_id, unit_vec, unit_vec)
    _insert_skill(db_conn, pr2_id, unit_vec, unit_vec)
    _insert_skill(db_conn, pr3_id, unit_vec, unit_vec)

    # No merged_before / merged_after -> all three PRs must be reachable
    results = await _search(
        pg_dsn_migrated,
        query_vec=unit_vec,
        top_n=10,
        oversample_factor=5,
        score_threshold=0.0,
        problem_weight=0.7,
        solution_weight=0.3,
        # merged_before and merged_after intentionally omitted
    )

    result_numbers = [r.number for r in results]
    for expected in (4001, 4002, 4003):
        assert expected in result_numbers, (
            f"PR #{expected} should appear when no window filter is set, "
            f"but result numbers were: {result_numbers}"
        )
