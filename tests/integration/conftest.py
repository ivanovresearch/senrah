"""
tests/integration/conftest.py — per-test DB isolation for the shared container.

The pgvector container (tests/conftest.py::pg_container) is session-scoped for
speed, but rows written by one test used to leak into the next (the documented
test-isolation defect: 11 tests green in isolation, red in a full run). This
autouse fixture truncates all four data tables BEFORE each test, so every test
starts from an empty, migrated schema regardless of what (or whether) the
previous test committed.

RESTART IDENTITY keeps generated ids deterministic; CASCADE handles the
skills→pull_requests→repositories→projects FK chain. lock_timeout turns a
"previous test left an open transaction" hang into a fast, diagnosable error.
"""

from __future__ import annotations

import psycopg
import pytest

_DATA_TABLES = "skills, pull_requests, repositories, projects"


@pytest.fixture(autouse=True)
def clean_tables(pg_dsn_migrated: str) -> None:
    """Truncate all data tables before each integration test."""
    with psycopg.connect(pg_dsn_migrated) as conn:
        conn.execute("SET lock_timeout = '5s'")
        conn.execute(f"TRUNCATE {_DATA_TABLES} RESTART IDENTITY CASCADE")
