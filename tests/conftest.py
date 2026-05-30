"""
tests/conftest.py — session-scoped pgvector testcontainer fixture.

Provides:
- pg_container: starts a pgvector/pgvector:pg17 container once per test session
- pg_dsn: psycopg3-compatible DSN for the container
- run_migrations: runs `alembic upgrade head` against the container
- fake_embedder: returns deterministic 1536-dim vectors (no OpenAI calls)

The pg_dsn fixture is consumed by all integration tests. Tests that need the
schema call pg_dsn_migrated (which runs migrations once per session).
"""

from __future__ import annotations

import os
import random
import subprocess
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer

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
