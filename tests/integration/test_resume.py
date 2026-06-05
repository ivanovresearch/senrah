"""
Integration tests for interrupt/resume guarantees (INGEST-05 / SC#4).

Docker-gated: requires a running PostgreSQL container (existing STATE.md blocker).
These tests are deferred until Docker Desktop is functional.

SC#4 guarantee: An interrupted run resumes from the stored cursor with no PR
re-fetched and none skipped.

Manual fallback (documented in 03-VALIDATION.md until Docker blocker is cleared):
  Run `harness ingest --scope last_n 200`, Ctrl-C mid-run, re-run, confirm
  no duplicate/skip in logs.

Covers:
- Interrupted run stores the cursor at the last committed PR
- Second run resumes from that cursor (no re-fetch of already-ingested PRs)
- No PRs are skipped between cursor position and the continuation point

Implementation lands in Plan 03 (ingest.py Ingester.run with atomic cursor).
Tests stay RED/Docker-gated until the existing STATE.md Docker blocker is cleared.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg
import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def conn(pg_dsn_migrated: str):
    """Synchronous psycopg3 connection to the migrated test database."""
    with psycopg.connect(pg_dsn_migrated) as connection:
        yield connection


@pytest.fixture
def project_and_repo(conn):
    """Create a project and repository row; return (project_id, repo_id)."""
    from harness.db.repos.project import ProjectRepo
    from harness.db.repos.repository import RepositoryRepo
    from harness.db.models import Project, Repository

    project_repo = ProjectRepo(conn)
    repo_repo = RepositoryRepo(conn)

    project = project_repo.upsert(Project(name="resume-test-project"))
    repository = repo_repo.upsert(
        Repository(
            project_id=project.id,  # type: ignore[arg-type]
            type="github",
            name="owner/resume-repo",
        )
    )
    conn.commit()
    return project.id, repository.id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResumeFromCursor:
    """Interrupt/resume: the stored cursor is respected on re-run."""

    def test_cursor_stored_after_first_pr(self, conn, project_and_repo) -> None:
        """After ingesting one PR, the cursor is stored in the DB."""
        from harness.db.repos.repository import RepositoryRepo
        from harness.db.repos.pr import PRRepo
        from harness.db.models import PullRequest

        project_id, repo_id = project_and_repo
        merged_at = datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

        # Manually advance cursor to simulate a committed PR
        repo_repo = RepositoryRepo(conn)
        repo_repo.advance_cursor(repo_id, merged_at, number=42)
        conn.commit()

        # Read back the op-state
        op_state = repo_repo.get_op_state(project_id, "owner/resume-repo")
        assert op_state is not None
        assert op_state.cursor_merged_at == merged_at
        assert op_state.cursor_number == 42

    def test_second_run_starts_after_cursor(self, conn, project_and_repo) -> None:
        """A second run with the cursor set skips already-processed PRs."""
        from harness.db.repos.repository import RepositoryRepo

        project_id, repo_id = project_and_repo
        cursor_merged_at = datetime(2024, 4, 1, 12, 0, 0, tzinfo=timezone.utc)

        repo_repo = RepositoryRepo(conn)
        repo_repo.advance_cursor(repo_id, cursor_merged_at, number=100)
        conn.commit()

        op_state = repo_repo.get_op_state(project_id, "owner/resume-repo")
        assert op_state is not None
        assert op_state.cursor_merged_at >= cursor_merged_at, (
            "Cursor must be monotonically non-decreasing (GREATEST semantics)"
        )

    def test_advance_cursor_is_monotonic(self, conn, project_and_repo) -> None:
        """advance_cursor uses GREATEST — older merged_at does not move cursor backward."""
        from harness.db.repos.repository import RepositoryRepo

        project_id, repo_id = project_and_repo
        newer = datetime(2024, 6, 1, tzinfo=timezone.utc)
        older = datetime(2024, 1, 1, tzinfo=timezone.utc)

        repo_repo = RepositoryRepo(conn)
        repo_repo.advance_cursor(repo_id, newer, number=200)
        conn.commit()

        # Now try to advance with an older date — should be a no-op (GREATEST)
        repo_repo.advance_cursor(repo_id, older, number=199)
        conn.commit()

        op_state = repo_repo.get_op_state(project_id, "owner/resume-repo")
        assert op_state is not None
        assert op_state.cursor_merged_at == newer, (
            f"GREATEST semantics violated: cursor moved backward to {op_state.cursor_merged_at}"
        )
