"""
Unit tests for GitHubConnector traversal (INGEST-05) and list_recent_merged_meta (INGEST-04).

Covers (Design B — backfill created-asc + incremental updated-desc):
- list_merged_prs BACKFILL (cursor=None): sort='created', direction='asc' spine
- list_merged_prs: PRs with merged_at < since are skipped
- list_merged_prs: last_n limits the count of yielded PRs
- list_merged_prs: unmerged (merged_at=None) PRs are dropped
- list_merged_prs: diff=None during traversal; diff_url endpoint never called
- (Incremental cursor mode + efficiency/N+1 live in test_traversal_incremental.py)
- list_recent_merged_meta: sort='updated', direction='desc' scan, true top-N by merged_at
- list_recent_merged_meta: skips unmerged, returns newest N PRMeta records
- list_recent_merged_meta: returns all if fewer than N available
- list_recent_merged_meta: no diff fetch (diff_url endpoint never called)
- list_recent_merged_meta: newest-N by merged_at (not arrival order) → window lower-bound
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from harness.connectors.base import PRMeta
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
    updated_at: datetime | None = None,
) -> MagicMock:
    """Build a minimal PyGithub PR mock for traversal tests."""
    pr = MagicMock()
    pr.number = number
    pr.title = f"PR #{number}"
    pr.body = f"Body of PR #{number}"
    pr.merged_at = merged_at
    pr.created_at = created_at or datetime(2024, 1, number, tzinfo=timezone.utc)
    # Design B incremental/meta scans read updated_at; default to merged_at
    # (a merge bumps updated_at, so updated_at >= merged_at in real data).
    pr.updated_at = updated_at or merged_at or pr.created_at
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
        """PRs with merged_at < since are skipped.

        With `since` set, the scan is updated-descending bounded by `since`
        (gate #1 / BUG C fix — no cursor). The list is updated-desc as the API
        returns it: the in-scope PR first, then the old one whose updated_at is
        below `since` (the break point).
        """
        since = datetime(2024, 3, 1, tzinfo=timezone.utc)
        new_pr = _make_pr_mock(2, merged_at=datetime(2024, 4, 1, tzinfo=timezone.utc))
        old_pr = _make_pr_mock(1, merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
        respx.get(DIFF_URL.format(2)).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [new_pr, old_pr]  # updated-desc
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


class TestListRecentMergedMeta:
    """list_recent_merged_meta must use descending scan; return newest-N PRMeta; no diff fetch."""

    def test_uses_updated_desc_scan(self) -> None:
        """get_pulls is called with sort='updated', direction='desc' (Design B:
        real newest-N-by-merged_at via an updated-desc scan, not a created proxy)."""
        prs = [
            _make_pr_mock(i, merged_at=datetime(2024, i, 1, tzinfo=timezone.utc))
            for i in range(1, 4)
        ]

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = prs
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            conn.list_recent_merged_meta("owner/repo", n=3)

            mock_repo.get_pulls.assert_called_once()
            call_kwargs = mock_repo.get_pulls.call_args
            kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
            args = call_kwargs.args if call_kwargs.args else ()
            assert kwargs.get("sort") == "updated" or "updated" in args, (
                f"Expected sort='updated', got call: {call_kwargs}"
            )
            assert kwargs.get("direction") == "desc" or "desc" in args, (
                f"Expected direction='desc', got call: {call_kwargs}"
            )

    def test_skips_unmerged_prs(self) -> None:
        """Unmerged (merged_at=None) PRs are skipped; only merged ones returned."""
        merged = _make_pr_mock(1, merged_at=datetime(2024, 3, 1, tzinfo=timezone.utc))
        unmerged = _make_pr_mock(2, merged_at=None)
        another_merged = _make_pr_mock(3, merged_at=datetime(2024, 5, 1, tzinfo=timezone.utc))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            # descending order: newest first
            mock_repo.get_pulls.return_value = [another_merged, unmerged, merged]
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            result = conn.list_recent_merged_meta("owner/repo", n=10)

        assert len(result) == 2
        numbers = {m.number for m in result}
        assert 2 not in numbers, "Unmerged PR must not appear in result"

    def test_returns_at_most_n_items(self) -> None:
        """Returns at most n items even when more merged PRs are available."""
        prs = [
            _make_pr_mock(i, merged_at=datetime(2024, i, 1, tzinfo=timezone.utc))
            for i in range(1, 6)  # 5 merged PRs
        ]

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = prs
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            result = conn.list_recent_merged_meta("owner/repo", n=3)

        assert len(result) == 3

    def test_returns_all_when_fewer_than_n(self) -> None:
        """When fewer than n merged PRs exist, returns all of them."""
        prs = [
            _make_pr_mock(i, merged_at=datetime(2024, i, 1, tzinfo=timezone.utc))
            for i in range(1, 3)  # only 2 merged PRs
        ]

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = prs
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            result = conn.list_recent_merged_meta("owner/repo", n=200)

        assert len(result) == 2

    def test_returns_pr_meta_objects(self) -> None:
        """Returns a list of PRMeta objects with number and merged_at."""
        merged_at = datetime(2024, 3, 15, tzinfo=timezone.utc)
        pr = _make_pr_mock(
            42, merged_at=merged_at, created_at=datetime(2024, 1, 15, tzinfo=timezone.utc)
        )

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [pr]
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            result = conn.list_recent_merged_meta("owner/repo", n=10)

        assert len(result) == 1
        meta = result[0]
        assert isinstance(meta, PRMeta)
        assert meta.number == 42
        assert meta.merged_at == merged_at

    @respx.mock
    def test_no_diff_fetch_during_meta_scan(self) -> None:
        """list_recent_merged_meta never fetches diffs (diff_url route never called)."""
        prs = [
            _make_pr_mock(i, merged_at=datetime(2024, i, 1, tzinfo=timezone.utc))
            for i in range(1, 4)
        ]
        # Register diff URL routes; they must not be called
        diff_routes = []
        for i in range(1, 4):
            diff_routes.append(
                respx.get(DIFF_URL.format(i)).mock(
                    return_value=httpx.Response(200, text=FAKE_DIFF)
                )
            )

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = prs
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            conn.list_recent_merged_meta("owner/repo", n=3)

        for route in diff_routes:
            assert route.call_count == 0, (
                "list_recent_merged_meta must not fetch any diffs (INGEST-03)"
            )

    def test_min_merged_at_is_last_n_window_lower_bound(self) -> None:
        """Newest-N is by MERGED_AT, not by scan-arrival order (INGEST-04).

        Discriminating input: updated_at diverges from merged_at, so an
        updated-desc scan arrives in a different order than merged_at order.
        Invariant honored (a merge bumps updated_at ⇒ updated_at >= merged_at).

        PRs, in updated-desc arrival order (as the API returns them):
          #1  merged Mar 1   updated Jun 10   (old merge, recently commented)
          #4  merged Apr 5   updated Jun 1    (commented later)
          #3  merged May 25  updated May 25
          #2  merged May 20  updated May 20
          #5  merged Jan 10  updated Jan 10

        Top-3 by MERGED_AT = {May 25(#3), May 20(#2), Apr 5(#4)} → min = Apr 5.
        A naive "first 3 as they arrive" would pick {#1, #4, #3} → min = Mar 1.
        This test fails on the latter (the created-desc / arrival-order proxy we
        previously got burned on) and passes only on true top-N-by-merged_at.
        """
        from datetime import timezone

        from harness.config import Scope, resolve_since

        def mk(number, merged, updated):
            return _make_pr_mock(
                number,
                merged_at=datetime(2024, *merged, tzinfo=timezone.utc),
                updated_at=datetime(2024, *updated, tzinfo=timezone.utc),
            )

        # Supplied in updated-descending order (the scan's contract)
        prs = [
            mk(1, (3, 1), (6, 10)),
            mk(4, (4, 5), (6, 1)),
            mk(3, (5, 25), (5, 25)),
            mk(2, (5, 20), (5, 20)),
            mk(5, (1, 10), (1, 10)),
        ]

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = prs
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            meta_list = conn.list_recent_merged_meta("owner/repo", n=3)

        assert len(meta_list) == 3
        # The newest-3 by merged_at are #3, #2, #4 — #1 (arrives first) is evicted.
        assert {m.number for m in meta_list} == {2, 3, 4}
        assert 1 not in {m.number for m in meta_list}, (
            "PR #1 arrived first (latest updated_at) but its merged_at is NOT in "
            "the top-3 — selecting by arrival order would wrongly keep it"
        )

        provider_dates = [m.merged_at for m in meta_list]
        assert min(provider_dates) == datetime(2024, 4, 5, tzinfo=timezone.utc)

        # Confirm resolve_since uses this as the last_n window lower-bound
        scope = Scope(mode="last_n", value=3)
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        since = resolve_since(scope, now=now, last_n_merged_at_provider=provider_dates)
        assert since == datetime(2024, 4, 5, tzinfo=timezone.utc)
