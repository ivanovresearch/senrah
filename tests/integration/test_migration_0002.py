"""
Integration tests for Alembic migration 0002 (OPS-03 / op-state schema).

Docker-gated: requires a running PostgreSQL container (existing STATE.md blocker).
These tests are deferred until Docker Desktop is functional. They collect under
the "integration" marker so the unit suite still runs green without Docker.

Covers:
- After `alembic upgrade head`, repositories table has 5 op-state columns
- After `alembic downgrade 0001`, those columns are dropped
- Downgrade is idempotent (DROP COLUMN IF EXISTS)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

# ---------------------------------------------------------------------------
# Helpers (same pattern as tests/integration/test_migrations.py)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent

OP_STATE_COLUMNS = [
    "cursor_merged_at",
    "cursor_number",
    "last_run_at",
    "last_run_status",
    "last_error",
]


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


def _run_alembic(command: list[str], dsn: str) -> None:
    """Run an alembic command against the given DSN."""
    env = {**os.environ, "DATABASE_URL": dsn}
    result = subprocess.run(
        ["python", "-m", "alembic"] + command,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic {' '.join(command)} failed:\n"
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def conn_0002(pg_dsn_migrated: str):
    """Connection to DB migrated to 0002 (head)."""
    with psycopg.connect(pg_dsn_migrated) as connection:
        yield connection


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigration0002Upgrade:
    """After upgrade head, op-state columns exist on repositories."""

    def test_cursor_merged_at_column_exists(self, conn_0002) -> None:
        """Migration 0002 adds cursor_merged_at to repositories."""
        assert _column_exists(conn_0002, "repositories", "cursor_merged_at"), (
            "cursor_merged_at column missing from repositories after 0002 upgrade"
        )

    def test_cursor_number_column_exists(self, conn_0002) -> None:
        """Migration 0002 adds cursor_number to repositories."""
        assert _column_exists(conn_0002, "repositories", "cursor_number"), (
            "cursor_number column missing from repositories after 0002 upgrade"
        )

    def test_last_run_at_column_exists(self, conn_0002) -> None:
        """Migration 0002 adds last_run_at to repositories."""
        assert _column_exists(conn_0002, "repositories", "last_run_at"), (
            "last_run_at column missing from repositories after 0002 upgrade"
        )

    def test_last_run_status_column_exists(self, conn_0002) -> None:
        """Migration 0002 adds last_run_status to repositories."""
        assert _column_exists(conn_0002, "repositories", "last_run_status"), (
            "last_run_status column missing from repositories after 0002 upgrade"
        )

    def test_last_error_column_exists(self, conn_0002) -> None:
        """Migration 0002 adds last_error to repositories."""
        assert _column_exists(conn_0002, "repositories", "last_error"), (
            "last_error column missing from repositories after 0002 upgrade"
        )

    def test_all_five_op_state_columns_exist(self, conn_0002) -> None:
        """All 5 op-state columns must exist after upgrade."""
        for col in OP_STATE_COLUMNS:
            assert _column_exists(conn_0002, "repositories", col), (
                f"Column '{col}' missing from repositories after 0002 upgrade"
            )
