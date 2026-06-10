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
from pgvector.psycopg import register_vector

REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(pg_dsn_migrated: str):
    """Synchronous psycopg3 connection to the migrated test database.

    Function-scoped (not module): trailing SELECTs leave an autobegin
    transaction open, and a module-scoped connection would hold its locks
    across tests — blocking the autouse clean_tables TRUNCATE.
    """
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


# ---------------------------------------------------------------------------
# BUG C — high-water cursor vs updated-desc resume DATA LOSS (RED gate)
# ---------------------------------------------------------------------------


@pytest.fixture
def ac_conn(pg_dsn_migrated: str):
    """Autocommit connection — mirrors the CLI's connect_sync(autocommit=True),
    which the Ingester REQUIRES so each per-PR conn.transaction() COMMITS durably
    (so a committed PR survives the simulated interrupt — D-B3)."""
    conn = psycopg.connect(pg_dsn_migrated)
    register_vector(conn)
    conn.autocommit = True
    yield conn
    conn.close()


class TestResumeDataLossBugC:
    """RED — proves BUG C (gate #1 blocker): the high-water `advance_cursor`
    cursor plus the `updated`-descending resume scan permanently skip a PR whose
    merged_at is low but whose updated_at places it LATE in the scan.

    Steady-state incremental window (prior cursor C0 = 2024-05-01):
      HIGH #2001: merged 2024-05-10, updated 2024-05-10  → scanned FIRST (newest
                  updated); committing it jumps the high-water cursor to May-10.
      LOW  #2002: merged 2024-05-02 (just above C0 → genuinely new, MUST ingest),
                  updated 2024-05-08 → scanned AFTER HIGH (updated-desc, non-
                  monotonic in merged_at).

    Clean (uninterrupted) run ingests BOTH. But if the run is interrupted after
    HIGH commits and before LOW, the DB cursor is now May-10 (high-water, NOT
    "contiguously processed up to here"). On resume the connector's bound =
    cursor.merged_at - overlap_margin = May-10 minus 1h = May-9 23:00; LOW.updated
    (May-8) < bound, so the updated-desc scan BREAKS before LOW and LOW is skipped
    forever. The gap dwarfs overlap_margin (1h), so the margin does NOT mask it —
    proving the defect is cursor SEMANTICS (high-water vs contiguous), not scan
    order and not the margin.

    This drives the REAL GitHubConnector._incremental_updated_desc, the REAL
    advance_cursor GREATEST SQL, and a REAL op-state read on resume. Only
    fetch_diff (diff content is irrelevant to C) and rate_limit_status are stubbed.

    Expected: RED on current code (resume loses #2002). Turns GREEN when the
    cursor becomes a contiguous low-watermark (or resume re-scans the scope).
    """

    def test_resume_skips_low_merged_late_updated_tail(self, ac_conn) -> None:
        from unittest.mock import MagicMock, patch

        from harness.connectors.base import RateLimitStatus
        from harness.db.models import Project, Repository
        from harness.db.repos.project import ProjectRepo
        from harness.db.repos.repository import RepositoryRepo
        from harness.ingester.ingest import Ingester

        UTC = timezone.utc
        C0 = datetime(2024, 5, 1, tzinfo=UTC)  # prior steady-state cursor

        def win_pr(number, merged_at, updated_at, author="dev"):
            pr = MagicMock()
            pr.number = number
            pr.title = f"PR #{number}"
            pr.body = f"Body #{number}"
            pr.merged_at = merged_at
            pr.updated_at = updated_at
            pr.created_at = datetime(2024, 4, 1, tzinfo=UTC)
            pr.additions = 10
            pr.deletions = 2
            pr.changed_files = 3  # not a giant PR
            pr.user = MagicMock()
            pr.user.login = author
            pr.diff_url = f"https://example.invalid/{number}.diff"
            return pr

        high = win_pr(2001, datetime(2024, 5, 10, tzinfo=UTC),
                      datetime(2024, 5, 10, tzinfo=UTC))
        low = win_pr(2002, datetime(2024, 5, 2, tzinfo=UTC),
                     datetime(2024, 5, 8, tzinfo=UTC))
        # updated-descending order, exactly as GitHub's list endpoint returns it
        window = [high, low]

        class DiffStub:
            """Returns a non-empty diff; simulates Ctrl-C at one PR number."""

            def __init__(self) -> None:
                self.interrupt_on: int | None = None

            def __call__(self, repo_full_name: str, number: int) -> str:
                if number == self.interrupt_on:
                    # BaseException (not Exception) → escapes the Ingester's
                    # per-PR `except Exception`, aborting the run mid-stream the
                    # way a real Ctrl-C / crash would.
                    raise KeyboardInterrupt(f"simulated interrupt at #{number}")
                return f"--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+x  # {number}\n"

        diff_stub = DiffStub()

        proj_repo = ProjectRepo(ac_conn)
        repo_repo = RepositoryRepo(ac_conn)
        project = proj_repo.upsert(Project(name="bugc-proj"))

        def seed_repo(name: str) -> int:
            r = repo_repo.upsert(
                Repository(project_id=project.id, type="github", name=name)  # type: ignore[arg-type]
            )
            repo_repo.advance_cursor(r.id, C0, number=900)  # prior cursor C0
            return r.id  # type: ignore[return-value]

        clean_repo_id = seed_repo("owner/bugc-clean")
        resume_repo_id = seed_repo("owner/bugc-resume")

        def ingested(repo_id: int) -> list[int]:
            rows = ac_conn.execute(
                "SELECT number FROM pull_requests WHERE repository_id = %(r)s",
                {"r": repo_id},
            ).fetchall()
            return sorted(row[0] for row in rows)

        with patch("harness.connectors.github.Github") as MockGithub:
            from harness.connectors.github import GitHubConnector

            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = window
            MockGithub.return_value.get_repo.return_value = mock_repo

            connector = GitHubConnector("ghp_fake_bugc_token")
            connector.fetch_diff = diff_stub  # type: ignore[method-assign]
            connector.rate_limit_status = lambda: RateLimitStatus(  # type: ignore[method-assign]
                remaining=5000, reset_at=datetime(2030, 1, 1, tzinfo=UTC), limit=5000
            )

            # (1) CLEAN run on its own repo — no interrupt: ingest the full window.
            diff_stub.interrupt_on = None
            Ingester(ac_conn).run(
                connector, "owner/bugc-clean", "bugc-proj", last_n=None, scope=None
            )
            s_clean = ingested(clean_repo_id)

            # (2) INTERRUPT run — Ctrl-C after HIGH commits, before LOW is fetched.
            diff_stub.interrupt_on = 2002
            with pytest.raises(KeyboardInterrupt):
                Ingester(ac_conn).run(
                    connector, "owner/bugc-resume", "bugc-proj", last_n=None, scope=None
                )

            # (3) RESUME run — no interrupt; cursor is now HIGH's high-water mark.
            diff_stub.interrupt_on = None
            Ingester(ac_conn).run(
                connector, "owner/bugc-resume", "bugc-proj", last_n=None, scope=None
            )
            s_resume = ingested(resume_repo_id)

        # Baseline sanity: the uninterrupted run ingests the whole window.
        assert s_clean == [2001, 2002], (
            f"clean baseline must ingest the whole window, got {s_clean}"
        )
        # The bug: resume drops the low-merged / late-updated tail PR permanently.
        missing = sorted(set(s_clean) - set(s_resume))
        assert s_resume == s_clean, (
            f"BUG C data loss — clean={s_clean} resume={s_resume}; resume "
            f"permanently skipped {missing}. The high-water cursor jumped to "
            f"#2001's merged_at on commit, so #2002 (lower merged_at, later "
            f"updated_at) fell below cursor-overlap_margin and the updated-desc "
            f"scan broke before reaching it."
        )


class TestResumeRecoversErroredPR:
    """RED — proves the *same* high-water family of loss for a PR that hit the
    Ingester's per-PR `except Exception` (logged & skipped) and never committed.

    First run: GOOD #3001 (merged 05-10) commits → high-water cursor jumps to
    May-10. BAD #3002 (merged 05-03) raises inside fetch_diff → caught by the
    per-PR isolation handler → logged & skipped → NOT in the DB. The run finishes
    "successfully" (per-PR errors don't abort it).

    Resume: under the high-water cursor, BAD's merged_at (May-3) is below
    cursor − overlap_margin, and its updated_at (May-8) is below the break bound,
    so the updated-desc scan never re-reaches it → BAD is lost forever.

    The present-in-DB probe fixes this for free: on resume the scope is re-scanned
    and BAD, being absent from pull_requests, is re-fetched and ingested — no
    separate freeze-on-error machinery required.

    Expected: RED on current code (BAD never ingested). GREEN after the fix.
    """

    def test_errored_pr_is_reingested_on_resume(self, ac_conn) -> None:
        from unittest.mock import MagicMock, patch

        from harness.connectors.base import RateLimitStatus
        from harness.db.models import Project, Repository
        from harness.db.repos.project import ProjectRepo
        from harness.db.repos.repository import RepositoryRepo
        from harness.ingester.ingest import Ingester

        UTC = timezone.utc

        def win_pr(number, merged_at, updated_at, author="dev"):
            pr = MagicMock()
            pr.number = number
            pr.title = f"PR #{number}"
            pr.body = f"Body #{number}"
            pr.merged_at = merged_at
            pr.updated_at = updated_at
            pr.created_at = datetime(2024, 4, 1, tzinfo=UTC)
            pr.additions = 10
            pr.deletions = 2
            pr.changed_files = 3
            pr.user = MagicMock()
            pr.user.login = author
            pr.diff_url = f"https://example.invalid/{number}.diff"
            return pr

        good = win_pr(3001, datetime(2024, 5, 10, tzinfo=UTC),
                      datetime(2024, 5, 10, tzinfo=UTC))
        bad = win_pr(3002, datetime(2024, 5, 3, tzinfo=UTC),
                     datetime(2024, 5, 8, tzinfo=UTC))
        window = [good, bad]  # updated-descending

        class DiffStub:
            """Returns a diff; raises a NORMAL Exception (per-PR isolation, not a
            run abort) for numbers in `fail_on`."""

            def __init__(self) -> None:
                self.fail_on: set[int] = set()

            def __call__(self, repo_full_name: str, number: int) -> str:
                if number in self.fail_on:
                    raise RuntimeError(f"simulated diff fetch failure for #{number}")
                return f"--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+x  # {number}\n"

        diff_stub = DiffStub()

        proj_repo = ProjectRepo(ac_conn)
        repo_repo = RepositoryRepo(ac_conn)
        project = proj_repo.upsert(Project(name="errored-proj"))
        repo = repo_repo.upsert(
            Repository(project_id=project.id, type="github", name="owner/errored-repo")  # type: ignore[arg-type]
        )
        repo_id = repo.id

        def ingested() -> list[int]:
            rows = ac_conn.execute(
                "SELECT number FROM pull_requests WHERE repository_id = %(r)s",
                {"r": repo_id},
            ).fetchall()
            return sorted(row[0] for row in rows)

        with patch("harness.connectors.github.Github") as MockGithub:
            from harness.connectors.github import GitHubConnector

            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = window
            MockGithub.return_value.get_repo.return_value = mock_repo

            connector = GitHubConnector("ghp_fake_errored_token")
            connector.fetch_diff = diff_stub  # type: ignore[method-assign]
            connector.rate_limit_status = lambda: RateLimitStatus(  # type: ignore[method-assign]
                remaining=5000, reset_at=datetime(2030, 1, 1, tzinfo=UTC), limit=5000
            )

            # (1) FIRST run — BAD fails inside fetch_diff (per-PR isolation), GOOD
            # commits and advances the high-water cursor past BAD's merged_at.
            diff_stub.fail_on = {3002}
            Ingester(ac_conn).run(
                connector, "owner/errored-repo", "errored-proj", last_n=None, scope=None
            )
            s_first = ingested()

            # (2) RESUME run — BAD now fetches fine; it must be re-ingested because
            # it is absent from pull_requests (probe sees "missing").
            diff_stub.fail_on = set()
            Ingester(ac_conn).run(
                connector, "owner/errored-repo", "errored-proj", last_n=None, scope=None
            )
            s_resume = ingested()

        assert s_first == [3001], (
            f"first run should ingest only GOOD (BAD errored), got {s_first}"
        )
        assert s_resume == [3001, 3002], (
            f"errored PR lost — after resume DB has {s_resume}, expected [3001, 3002]. "
            f"The high-water cursor advanced past #3002 on GOOD's commit, so resume "
            f"never re-reached the errored PR. The present-in-DB probe must re-ingest it."
        )
