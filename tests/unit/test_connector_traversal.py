"""
Unit tests for GitHubConnector.list_merged_prs created-asc traversal + cursor (INGEST-05).

Covers:
- PRs are iterated in sort="created", direction="asc" order
- PRs with merged_at < since are skipped
- Cursor (PRCursor) is respected: PRs at/below cursor.merged_at are skipped
- last_n limits the count of yielded PRs
- All yielded PRs have merged_at set (unmerged are dropped)

Implementation lands in Plan 02 (github.py traversal rewrite).
Tests are written to assert documented behavior so they go RED now and GREEN
when Plan 02 lands.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import respx
import httpx

from harness.connectors.base import PRCursor, RawPR
from harness.connectors.github import GitHubConnector


FAKE_TOKEN = "ghp_fake_connector_traversal_token_12345"
DIFF_URL = "https://github.com/owner/repo/pull/{}.diff"
FAKE_DIFF = "diff --git a/foo.py b/foo.py\n+new\n"


def _make_pr_mock(
    number: int,
    merged_at: datetime | None,
    created_at: datetime | None = None,
    changed_files: int = 5,
    additions: int = 10,
    deletions: int = 3,
    author: str = "contributor",
) -> MagicMock:
    """Build a minimal PyGithub PR mock for traversal tests."""
    pr = MagicMock()
    pr.number = number
    pr.title = f"PR #{number}"
    pr.body = f"Body of PR #{number}"
    pr.merged_at = merged_at
    pr.created_at = created_at or datetime(2024, 1, number, tzinfo=timezone.utc)
    pr.diff_url = DIFF_URL.format(number)
    pr.additions = additions
    pr.deletions = deletions
    pr.changed_files = changed_files
    pr.user = MagicMock()
    pr.user.login = author
    pr.get_files.return_value = []
    return pr


@pytest.fixture
def connector() -> GitHubConnector:
    with patch("harness.connectors.github.Github"):
        return GitHubConnector(FAKE_TOKEN)


class TestCreatedAscTraversal:
    """list_merged_prs must use sort='created', direction='asc'."""

    @respx.mock
    def test_passes_created_asc_to_get_pulls(self) -> None:
        """get_pulls is called with sort='created', direction='asc'."""
        pr = _make_pr_mock(1, merged_at=datetime(2024, 3, 1, tzinfo=timezone.utc))
        respx.get(DIFF_URL.format(1)).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [pr]
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            list(conn.list_merged_prs("owner/repo"))

            mock_repo.get_pulls.assert_called_once()
            call_kwargs = mock_repo.get_pulls.call_args
            # Sort must be "created", direction must be "asc"
            kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
            args = call_kwargs.args if call_kwargs.args else ()
            # Allow positional or keyword args
            assert kwargs.get("sort") == "created" or "created" in args, (
                f"Expected sort='created', got call: {call_kwargs}"
            )
            assert kwargs.get("direction") == "asc" or "asc" in args, (
                f"Expected direction='asc', got call: {call_kwargs}"
            )

    @respx.mock
    def test_skips_prs_below_since(self) -> None:
        """PRs with merged_at < since are skipped."""
        since = datetime(2024, 3, 1, tzinfo=timezone.utc)
        old_pr = _make_pr_mock(1, merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
        new_pr = _make_pr_mock(2, merged_at=datetime(2024, 4, 1, tzinfo=timezone.utc))
        respx.get(DIFF_URL.format(2)).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [old_pr, new_pr]
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            results = list(conn.list_merged_prs("owner/repo", since=since))

        assert len(results) == 1
        assert results[0].number == 2

    @respx.mock
    def test_skips_unmerged_prs(self) -> None:
        """PRs without merged_at are always skipped."""
        merged = _make_pr_mock(1, merged_at=datetime(2024, 3, 1, tzinfo=timezone.utc))
        unmerged = _make_pr_mock(2, merged_at=None)
        respx.get(DIFF_URL.format(1)).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [merged, unmerged]
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            results = list(conn.list_merged_prs("owner/repo"))

        assert len(results) == 1
        assert results[0].number == 1

    @respx.mock
    def test_last_n_limits_count(self) -> None:
        """last_n caps the number of yielded PRs."""
        prs = [
            _make_pr_mock(i, merged_at=datetime(2024, i, 1, tzinfo=timezone.utc))
            for i in range(1, 6)
        ]
        for i in range(1, 6):
            respx.get(DIFF_URL.format(i)).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = prs
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            results = list(conn.list_merged_prs("owner/repo", last_n=3))

        assert len(results) == 3
