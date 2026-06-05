"""
tests/conftest.py — session-scoped pgvector testcontainer fixture.

Provides:
- pg_container: starts a pgvector/pgvector:pg17 container once per test session
- pg_dsn: psycopg3-compatible DSN for the container
- run_migrations: runs `alembic upgrade head` against the container
- fake_embedder: returns deterministic 1536-dim vectors (no OpenAI calls)
- paginated_get_pulls_factory: respx-based factory for paginated PR metadata
- fake_pr_metadata: builds fake PyGithub PR metadata objects (no network)

The pg_dsn fixture is consumed by all integration tests. Tests that need the
schema call pg_dsn_migrated (which runs migrations once per session).
"""

from __future__ import annotations

import asyncio
import os
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from testcontainers.postgres import PostgresContainer

# psycopg3 async cannot run on Windows' default ProactorEventLoop. Integration
# and E2E tests that exercise the async pool (e.g. SkillRepo.search) require the
# SelectorEventLoop on Windows — mirror the CLI entry point's policy here so
# `pytest`/`pytest -m e2e` works on Windows. No-op on other platforms.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Repo root: two levels up from tests/
REPO_ROOT = Path(__file__).parent.parent

# pgvector Docker image — must NOT be plain postgres (Pitfall 2)
PGVECTOR_IMAGE = "pgvector/pgvector:pg17"


@pytest.fixture(scope="session")
def pg_container():
    """Start a pgvector container for the entire test session."""
    with PostgresContainer(PGVECTOR_IMAGE) as container:
        yield container


@pytest.fixture(scope="session")
def pg_dsn(pg_container) -> str:
    """Return a psycopg3-compatible DSN for the test container.

    testcontainers returns a SQLAlchemy-style URL (postgresql://...).
    We convert to the psycopg3 format (postgresql+psycopg is NOT needed for
    psycopg.connect; just use plain postgresql://).
    For alembic (which uses SQLAlchemy internally), we keep the postgresql:// form.
    """
    url = pg_container.get_connection_url()
    # testcontainers may return postgresql+psycopg2:// — normalise to postgresql://
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    url = url.replace("postgresql+psycopg://", "postgresql://")
    return url


@pytest.fixture(scope="session")
def pg_dsn_migrated(pg_dsn: str) -> str:
    """Run alembic upgrade head once per session, then yield the DSN.

    All integration tests that need the schema should use this fixture instead
    of pg_dsn directly.
    """
    env = {**os.environ, "DATABASE_URL": pg_dsn}
    result = subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
    return pg_dsn


@pytest.fixture
def fake_embedder():
    """Return a function that generates deterministic 1536-dim unit vectors.

    Uses a seeded RNG keyed on the input text so the same text always produces
    the same vector (deterministic across test runs), with no OpenAI API calls.
    The vectors are normalised to unit length (appropriate for cosine similarity).
    """

    def _embed(text: str) -> list[float]:
        rng = random.Random(hash(text) & 0xFFFF_FFFF)
        raw = [rng.gauss(0, 1) for _ in range(1536)]
        # Normalise to unit length for cosine similarity
        magnitude = sum(x * x for x in raw) ** 0.5
        if magnitude == 0:
            return [0.0] * 1536
        return [x / magnitude for x in raw]

    return _embed


# ---------------------------------------------------------------------------
# Phase 3: shared respx/metadata fixtures for connector + ingest tests
# ---------------------------------------------------------------------------


def _make_mock_pr_metadata(
    number: int,
    merged_at: datetime | None = None,
    author: str = "contributor",
    changed_files: int = 5,
    additions: int = 10,
    deletions: int = 3,
    diff_url: str | None = None,
    title: str | None = None,
    body: str | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    """Build a fake PyGithub PullRequest metadata object (no real network).

    Produces objects with the fields the GitHubConnector reads:
    number, merged_at, user.login, changed_files (int), additions, deletions, diff_url.
    Used by connector traversal, diff-retry, and ingest filter tests.

    No real token or DSN; token-free per test hygiene rules.
    """
    pr = MagicMock()
    pr.number = number
    pr.title = title or f"PR #{number}"
    pr.body = body or f"Body of PR #{number}"
    pr.merged_at = merged_at or datetime(2024, (number % 12) + 1, 1, tzinfo=timezone.utc)
    pr.created_at = created_at or datetime(2024, (number % 12) + 1, 1, tzinfo=timezone.utc)
    pr.diff_url = diff_url or f"https://github.com/owner/repo/pull/{number}.diff"
    pr.additions = additions
    pr.deletions = deletions
    pr.changed_files = changed_files  # integer count (cheap metadata, no list fetch)
    pr.user = MagicMock()
    pr.user.login = author
    # get_files() returns file mocks — default to empty (tests override if needed)
    pr.get_files.return_value = []
    return pr


@pytest.fixture
def fake_pr_metadata():
    """Return the _make_mock_pr_metadata factory.

    Usage:
        def test_something(fake_pr_metadata):
            pr = fake_pr_metadata(number=1, author="alice")
    """
    return _make_mock_pr_metadata


@pytest.fixture
def paginated_get_pulls_factory():
    """Return a factory that produces a controllable sequence of fake PR mocks.

    The factory takes a list of PR metadata kwargs dicts and returns a list of
    MagicMock PR objects that can be assigned to mock_repo.get_pulls.return_value.
    Designed for created-asc traversal + cursor tests in Plans 02/03.

    Usage:
        def test_traversal(paginated_get_pulls_factory):
            prs = paginated_get_pulls_factory([
                {"number": 1, "merged_at": datetime(2024, 1, 1, tzinfo=timezone.utc)},
                {"number": 2, "merged_at": datetime(2024, 2, 1, tzinfo=timezone.utc), "author": "bot[bot]"},
            ])
            # prs is a list of MagicMock objects ordered as given (created-asc)
    """
    def _factory(pr_kwargs_list: list[dict[str, Any]]) -> list[MagicMock]:
        return [_make_mock_pr_metadata(**kwargs) for kwargs in pr_kwargs_list]

    return _factory
