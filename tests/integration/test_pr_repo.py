"""
Integration tests for senrah.db.repos.pr.PRRepo.

Uses the testcontainers pg_dsn_migrated fixture (from conftest.py) to run
against a real pgvector container with the full schema applied.

Tests assert:
- PRRepo.upsert persists diff and files_changed (STORE-02 link)
- Re-upserting the same (repository_id, number) updates rather than duplicates
  (row count stays 1 after multiple upserts)
- content_hash is populated correctly
- linked_issue and other optional fields are stored and retrieved correctly

Note: These tests require Docker to spin up a pgvector container.
If Docker is unavailable, they will fail with a container startup error.
Per plan instructions, mark as DEFERRED if Docker is the sole blocker.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import psycopg
import pytest

from senrah.db.models import Project, PullRequest, Repository
from senrah.db.repos.pr import PRRepo, _compute_content_hash
from senrah.db.repos.project import ProjectRepo
from senrah.db.repos.repository import RepositoryRepo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(pg_dsn_migrated: str):
    """Synchronous psycopg3 connection to the migrated test database."""
    with psycopg.connect(pg_dsn_migrated) as connection:
        yield connection


@pytest.fixture
def repository_id(conn) -> int:
    """Create a project + repository row and return the repository id."""
    project_repo = ProjectRepo(conn)
    repo_repo = RepositoryRepo(conn)

    project = project_repo.upsert(Project(name="test-project"))
    repository = repo_repo.upsert(
        Repository(
            project_id=project.id,  # type: ignore[arg-type]
            type="github",
            name="owner/test-repo",
        )
    )
    return repository.id  # type: ignore[return-value]


def _make_pr(repository_id: int, number: int = 1, **overrides) -> PullRequest:
    """Build a minimal PullRequest for testing."""
    defaults = dict(
        repository_id=repository_id,
        number=number,
        title="Fix cursor pagination",
        body="Closes #100",
        diff="diff --git a/foo.py b/foo.py\n+new line\n",
        author="jkotas",
        merged_at=datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc),
        linked_issue="#100",
        files_changed=["src/foo.py", "src/bar.py"],
    )
    defaults.update(overrides)
    return PullRequest(**defaults)


# ---------------------------------------------------------------------------
# upsert tests
# ---------------------------------------------------------------------------


class TestPRRepoUpsert:
    def test_upsert_stores_diff(self, conn, repository_id: int) -> None:
        """STORE-02: diff is persisted in the pull_requests row."""
        pr = _make_pr(repository_id, diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n")
        pr_repo = PRRepo(conn)
        row_id = pr_repo.upsert(pr)

        row = conn.execute(
            "SELECT diff FROM pull_requests WHERE id = %(id)s",
            {"id": row_id},
        ).fetchone()

        assert row is not None
        assert row[0] == pr.diff

    def test_upsert_stores_files_changed(self, conn, repository_id: int) -> None:
        """files_changed list is stored as JSONB and retrieved correctly."""
        files = ["src/foo.py", "src/bar.py", "tests/test_foo.py"]
        pr = _make_pr(repository_id, files_changed=files)
        pr_repo = PRRepo(conn)
        row_id = pr_repo.upsert(pr)

        row = conn.execute(
            "SELECT files_changed FROM pull_requests WHERE id = %(id)s",
            {"id": row_id},
        ).fetchone()

        assert row is not None
        # psycopg3 returns JSONB as a Python object (already parsed)
        stored_files = row[0]
        assert stored_files == files

    def test_upsert_conflict_updates_not_duplicates(
        self, conn, repository_id: int
    ) -> None:
        """Re-upserting the same (repository_id, number) updates in place.

        Row count stays 1 after multiple upserts.
        """
        pr_repo = PRRepo(conn)
        pr = _make_pr(repository_id, number=42, title="Original title")

        id1 = pr_repo.upsert(pr)

        # Update the title and upsert again
        pr_updated = _make_pr(
            repository_id, number=42, title="Updated title"
        )
        id2 = pr_repo.upsert(pr_updated)

        # Verify only one row exists
        count = conn.execute(
            """
            SELECT COUNT(*) FROM pull_requests
            WHERE repository_id = %(repo_id)s AND number = %(number)s
            """,
            {"repo_id": repository_id, "number": 42},
        ).fetchone()[0]

        assert count == 1, f"Expected 1 row, got {count}"

        # Verify the title was updated
        row = conn.execute(
            "SELECT title FROM pull_requests WHERE id = %(id)s",
            {"id": id1},
        ).fetchone()
        assert row[0] == "Updated title"

    def test_upsert_returns_row_id(self, conn, repository_id: int) -> None:
        """upsert returns the integer row id."""
        pr = _make_pr(repository_id)
        pr_repo = PRRepo(conn)
        row_id = pr_repo.upsert(pr)

        assert isinstance(row_id, int)
        assert row_id > 0

    def test_upsert_stores_content_hash(self, conn, repository_id: int) -> None:
        """content_hash is stored and matches SHA-256 of title+body+diff."""
        pr = _make_pr(repository_id)
        pr_repo = PRRepo(conn)
        row_id = pr_repo.upsert(pr)

        row = conn.execute(
            "SELECT content_hash FROM pull_requests WHERE id = %(id)s",
            {"id": row_id},
        ).fetchone()

        expected_hash = _compute_content_hash(pr.title, pr.body or "", pr.diff or "")
        assert row[0] == expected_hash

    def test_upsert_stores_linked_issue(self, conn, repository_id: int) -> None:
        """linked_issue is stored correctly."""
        pr = _make_pr(repository_id, linked_issue="#42")
        pr_repo = PRRepo(conn)
        row_id = pr_repo.upsert(pr)

        row = conn.execute(
            "SELECT linked_issue FROM pull_requests WHERE id = %(id)s",
            {"id": row_id},
        ).fetchone()

        assert row[0] == "#42"

    def test_upsert_allows_null_linked_issue(self, conn, repository_id: int) -> None:
        """linked_issue can be None (not all PRs reference an issue)."""
        pr = _make_pr(repository_id, linked_issue=None)
        pr_repo = PRRepo(conn)
        row_id = pr_repo.upsert(pr)

        row = conn.execute(
            "SELECT linked_issue FROM pull_requests WHERE id = %(id)s",
            {"id": row_id},
        ).fetchone()

        assert row[0] is None

    def test_upsert_stores_empty_files_changed(
        self, conn, repository_id: int
    ) -> None:
        """files_changed=[] is stored correctly as empty JSON array."""
        pr = _make_pr(repository_id, files_changed=[])
        pr_repo = PRRepo(conn)
        row_id = pr_repo.upsert(pr)

        row = conn.execute(
            "SELECT files_changed FROM pull_requests WHERE id = %(id)s",
            {"id": row_id},
        ).fetchone()

        assert row[0] == []

    def test_upsert_multiple_prs_different_numbers(
        self, conn, repository_id: int
    ) -> None:
        """Multiple PRs with different numbers each get their own row."""
        pr_repo = PRRepo(conn)
        id1 = pr_repo.upsert(_make_pr(repository_id, number=1))
        id2 = pr_repo.upsert(_make_pr(repository_id, number=2))
        id3 = pr_repo.upsert(_make_pr(repository_id, number=3))

        assert id1 != id2 != id3

        count = conn.execute(
            "SELECT COUNT(*) FROM pull_requests WHERE repository_id = %(repo_id)s",
            {"repo_id": repository_id},
        ).fetchone()[0]
        assert count == 3


# ---------------------------------------------------------------------------
# unindexed_prs tests
# ---------------------------------------------------------------------------


class TestPRRepoUnindexedPrs:
    def test_returns_prs_without_skills_rows(
        self, conn, repository_id: int
    ) -> None:
        """unindexed_prs returns PRs with no corresponding skills row."""
        pr_repo = PRRepo(conn)
        pr_repo.upsert(_make_pr(repository_id, number=10))
        pr_repo.upsert(_make_pr(repository_id, number=11))

        unindexed = pr_repo.unindexed_prs(repository_id)

        assert len(unindexed) == 2
        numbers = {pr.number for pr in unindexed}
        assert numbers == {10, 11}

    def test_returns_empty_when_all_indexed(
        self, conn, repository_id: int
    ) -> None:
        """unindexed_prs returns [] when all PRs have skills rows."""
        pr_repo = PRRepo(conn)
        row_id = pr_repo.upsert(_make_pr(repository_id, number=20))

        # Insert a matching skills row so this PR is "indexed"
        conn.execute(
            """
            INSERT INTO skills (pr_id, embedding_model, embedding_version)
            VALUES (%(pr_id)s, 'text-embedding-3-small', 'v1')
            """,
            {"pr_id": row_id},
        )

        unindexed = pr_repo.unindexed_prs(repository_id)
        assert unindexed == []

    def test_returns_only_unindexed(self, conn, repository_id: int) -> None:
        """unindexed_prs returns only PRs without skills, not indexed ones."""
        pr_repo = PRRepo(conn)
        indexed_id = pr_repo.upsert(_make_pr(repository_id, number=30))
        pr_repo.upsert(_make_pr(repository_id, number=31))

        # Mark PR 30 as indexed
        conn.execute(
            """
            INSERT INTO skills (pr_id, embedding_model, embedding_version)
            VALUES (%(pr_id)s, 'text-embedding-3-small', 'v1')
            """,
            {"pr_id": indexed_id},
        )

        unindexed = pr_repo.unindexed_prs(repository_id)
        assert len(unindexed) == 1
        assert unindexed[0].number == 31
