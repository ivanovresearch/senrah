"""
Incremental-traversal tests for GitHubConnector (Design B — supersedes the
created-asc full-scan accepted "at MVP" in RESEARCH Pattern 1 / Pitfall 1).

Design B contract:
- Backfill (cursor is None): created-asc forward spine (unchanged, correct once).
- Incremental (cursor set): traverse sort="updated", direction="desc"; yield
  merged PRs with merged_at > (cursor.merged_at - overlap_margin); BREAK as soon
  as updated_at < (cursor.merged_at - overlap_margin). Because a merge bumps
  updated_at (updated_at >= merged_at), no PR merged after the cursor is missed,
  and the scan stops at the cursor window instead of walking all history.

Two cases, deliberately separated:
  A. Correctness (back-dating): a PR created early but merged recently (above the
     cursor) is still yielded; an old already-ingested PR merely re-touched
     (merged below cursor, updated recently) is NOT yielded. Behavioural guard —
     must stay green across the rewrite.
  B. Efficiency + N+1: counts REAL GETs at PyGithub's Requester layer (where the
     per-PR completion fetch for additions/deletions is visible — MagicMock PR
     objects hide it). Asserts the incremental scan stops at the cursor window
     (does NOT paginate the whole history). RED on the current created-asc code
     (no break in incremental mode → paginates every page).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from harness.connectors.base import RateLimitStatus

FAKE_TOKEN = "ghp_fake_incremental_traversal_token_12345"
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Case A — back-dating correctness (MagicMock get_pulls; content assertions)
# ---------------------------------------------------------------------------


def _mock_pr(number, *, merged_at, updated_at, created_at, author="dev"):
    pr = MagicMock()
    pr.number = number
    pr.title = f"PR #{number}"
    pr.body = f"Body #{number}"
    pr.merged_at = merged_at
    pr.updated_at = updated_at
    pr.created_at = created_at
    pr.additions = 10
    pr.deletions = 2
    pr.changed_files = 3
    pr.user = MagicMock()
    pr.user.login = author
    pr.diff_url = f"https://github.com/owner/repo/pull/{number}.diff"
    return pr


class TestBackDatingCorrectness:
    """Incremental traversal must catch back-dated merges and skip re-touched old PRs."""

    def test_backdated_merge_yielded_retouched_old_skipped(self) -> None:
        # `since` is the scope lower bound (no cursor — gate #1 / BUG C fix).
        cursor_merged = datetime(2024, 5, 1, tzinfo=UTC)

        # Back-dated: created long ago, merged AFTER `since` → must be yielded.
        backdated = _mock_pr(
            42,
            merged_at=datetime(2024, 5, 3, tzinfo=UTC),
            updated_at=datetime(2024, 5, 3, tzinfo=UTC),
            created_at=datetime(2023, 1, 1, tzinfo=UTC),
        )
        # Old PR merged well below the cursor but re-touched (comment) recently →
        # appears early in updated-desc, but is already ingested → must NOT yield.
        retouched = _mock_pr(
            7,
            merged_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 5, 4, tzinfo=UTC),
            created_at=datetime(2023, 12, 1, tzinfo=UTC),
        )
        # Genuinely old & untouched → below the break bound.
        old = _mock_pr(
            3,
            merged_at=datetime(2023, 6, 1, tzinfo=UTC),
            updated_at=datetime(2023, 6, 1, tzinfo=UTC),
            created_at=datetime(2023, 5, 1, tzinfo=UTC),
        )
        # updated-desc order as the API would return it
        ordered = [retouched, backdated, old]

        with patch("harness.connectors.github.Github") as MockGithub:
            from harness.connectors.github import GitHubConnector

            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = ordered
            MockGithub.return_value.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            results = list(conn.list_merged_prs("owner/repo", since=cursor_merged))

        numbers = [r.number for r in results]
        assert 42 in numbers, "back-dated merge (above `since`) must be yielded"
        assert 7 not in numbers, "PR merged below `since` must NOT be yielded"
        assert 3 not in numbers, "old untouched PR must NOT be yielded"


# ---------------------------------------------------------------------------
# Case B — efficiency + N+1 (real Requester-layer GET counting)
# ---------------------------------------------------------------------------


class _FakeGitHubAPI:
    """Patches PyGithub's Requester to serve paginated PR summaries from memory
    and count real GETs (list pages + per-PR completion fetches).

    Summary payloads deliberately omit additions/deletions/changed_files (as the
    real list endpoint does) so accessing them on a yielded PR triggers a
    completion GET — making the N+1 observable.
    """

    def __init__(self, pages: list[list[dict]], detailed_changed_files: int = 3):
        self.pages = pages
        self.list_gets = 0
        self.completion_gets = 0
        self._detailed_changed_files = detailed_changed_files
        self._by_number = {pr["number"]: pr for page in pages for pr in page}

    def _detailed(self, n: int) -> dict:
        d = dict(self._by_number[n])
        d.update({"additions": 10, "deletions": 2,
                  "changed_files": self._detailed_changed_files})
        return d

    def __call__(self, requester, verb, url, parameters=None, headers=None,
                 input=None, follow_302_redirect=False):
        path = url.split("?", 1)[0]
        # get_repo → /repos/owner/repo
        if path.endswith("/repos/owner/repo"):
            return ({}, {"url": "https://api.github.com/repos/owner/repo",
                         "name": "repo", "full_name": "owner/repo"})
        # completion fetch → /repos/owner/repo/pulls/<number>
        tail = path.rstrip("/").rsplit("/pulls/", 1)
        if len(tail) == 2 and tail[1].isdigit():
            self.completion_gets += 1
            return ({}, self._detailed(int(tail[1])))
        # list page → /repos/owner/repo/pulls   (page via ?page=N)
        if path.rstrip("/").endswith("/pulls"):
            self.list_gets += 1
            page_num = 1
            if "page=" in url:
                page_num = int(url.split("page=", 1)[1].split("&", 1)[0])
            page = self.pages[page_num - 1]
            resp_headers = {}
            if page_num < len(self.pages):
                nxt = f"https://api.github.com/repos/owner/repo/pulls?page={page_num + 1}"
                resp_headers["link"] = f'<{nxt}>; rel="next"'
            return (resp_headers, page)
        return ({}, {})


def _summary(
    number: int,
    *,
    merged_at: datetime,
    updated_at: datetime,
    created_at: datetime | None = None,
    author: str = "dev",
) -> dict:
    created = created_at or datetime(2023, 1, 1, tzinfo=UTC)
    return {
        "url": f"https://api.github.com/repos/owner/repo/pulls/{number}",
        "id": number, "number": number, "state": "closed",
        "title": f"PR {number}", "body": "body",
        "user": {"login": author, "id": 1,
                 "url": f"https://api.github.com/users/{author}"},
        "merged_at": merged_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at": updated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "diff_url": f"https://github.com/owner/repo/pull/{number}.diff",
        # NO additions/deletions/changed_files — forces a completion GET (N+1)
    }


@pytest.fixture
def fake_api():
    import github.Requester as Rmod

    holder = {}

    def install(pages, **kwargs):
        api = _FakeGitHubAPI(pages, **kwargs)
        holder["api"] = api
        return api

    def dispatch(self, *args, **kw):
        return holder["api"](self, *args, **kw)

    with patch.object(Rmod.Requester, "requestJsonAndCheck", dispatch):
        yield install


class TestIncrementalEfficiency:
    """Incremental scan must stop at the cursor window, not walk all history.

    RED on the current created-asc code: it has no break in incremental mode and
    paginates every page (list_gets == number of pages).
    """

    def test_incremental_stops_at_cursor_window(self, fake_api) -> None:
        # `since` is the scope lower bound that bounds the updated-desc scan.
        cursor_merged = datetime(2024, 5, 1, tzinfo=UTC)

        # Page 1: newest by updated_at, merged ABOVE `since` → the real delta.
        page1 = [
            _summary(1001, merged_at=datetime(2024, 5, 4, tzinfo=UTC),
                     updated_at=datetime(2024, 5, 4, tzinfo=UTC)),
            _summary(1000, merged_at=datetime(2024, 5, 2, tzinfo=UTC),
                     updated_at=datetime(2024, 5, 2, tzinfo=UTC)),
        ]
        # Pages 2-4: old PRs, updated_at well BELOW the bound → new code must
        # break upon entering page 2 and never fetch pages 3-4.
        old = lambda n, d: _summary(  # noqa: E731
            n, merged_at=datetime(2024, 1, d, tzinfo=UTC),
            updated_at=datetime(2024, 1, d, tzinfo=UTC))
        page2 = [old(800, 9), old(799, 8)]
        page3 = [old(700, 7), old(699, 6)]
        page4 = [old(600, 5), old(599, 4)]
        api = fake_api([page1, page2, page3, page4])

        from harness.connectors.github import GitHubConnector

        conn = GitHubConnector(FAKE_TOKEN)
        results = list(conn.list_merged_prs("owner/repo", since=cursor_merged))

        nums = sorted(r.number for r in results)
        # Only the two above-`since` PRs are yielded.
        assert nums == [1000, 1001], f"expected the scope-window delta, got {nums}"
        # Efficiency: must NOT paginate the whole history. New code reads page 1,
        # peeks page 2 to discover the break → at most 2 list GETs. Current
        # created-asc code reads all 4 pages → FAILS here (this is the RED gate).
        assert api.list_gets <= 2, (
            f"incremental scan paginated {api.list_gets} pages — it must stop at "
            "the cursor window (<=2), not walk the whole history"
        )
        # Deferral (Finding 2 fix): the traversal itself fires ZERO completion
        # GETs — the giant-filter fields (additions/deletions/changed_files) are
        # read lazily via RawPR.size(), not at yield time.
        assert api.completion_gets == 0, (
            f"traversal must not fire completion GETs (deferred to size()); "
            f"got {api.completion_gets}"
        )
        # size() is where the per-PR completion GET fires — exactly one per PR.
        for r in results:
            r.size()
        assert api.completion_gets == len(results), (
            "size() fires exactly one completion GET per PR (the N+1, now deferred)"
        )
        assert all(r.diff is None for r in results), "traversal must not fetch diffs"

    def test_backdated_pr_yielded_without_full_scan(self, fake_api) -> None:
        """A back-dated merge (created long ago, merged just above the cursor,
        updated recently) is yielded AND the scan still stops at the cursor
        window — measured by real list-GET count, not a single MagicMock page.

        RED on the old created-asc code: it walks all 4 pages (list_gets == 4).
        """
        cursor_merged = datetime(2024, 5, 1, tzinfo=UTC)

        # Page 1 (updated-desc): the back-dated PR appears first because its
        # updated_at is the most recent, even though it was created in 2022 and
        # merged only just above `since`.
        backdated = _summary(
            1002,
            merged_at=datetime(2024, 5, 2, tzinfo=UTC),    # just above `since`
            updated_at=datetime(2024, 5, 10, tzinfo=UTC),  # latest activity
            created_at=datetime(2022, 3, 1, tzinfo=UTC),   # long-lived branch
        )
        normal = _summary(
            1001,
            merged_at=datetime(2024, 5, 4, tzinfo=UTC),
            updated_at=datetime(2024, 5, 4, tzinfo=UTC),
        )
        page1 = [backdated, normal]
        old = lambda n, d: _summary(  # noqa: E731
            n, merged_at=datetime(2024, 1, d, tzinfo=UTC),
            updated_at=datetime(2024, 1, d, tzinfo=UTC))
        api = fake_api([page1, [old(800, 9), old(799, 8)],
                        [old(700, 7), old(699, 6)], [old(600, 5), old(599, 4)]])

        from harness.connectors.github import GitHubConnector

        conn = GitHubConnector(FAKE_TOKEN)
        results = list(conn.list_merged_prs("owner/repo", since=cursor_merged))

        nums = sorted(r.number for r in results)
        # (a) the back-dated PR is yielded
        assert 1002 in nums, "back-dated merge (above `since`) must be yielded"
        assert nums == [1001, 1002], f"unexpected yield set {nums}"
        # (b) the traversal did NOT go full-scan
        assert api.list_gets <= 2, (
            f"scan paginated {api.list_gets} pages — must stop at the scope "
            "window even with a back-dated PR present"
        )
        # Deferral (Finding 2 fix): no completion GET during traversal itself.
        assert api.completion_gets == 0


class TestRecentMergedMetaEfficiency:
    """list_recent_merged_meta must stop at the recent-activity window via the
    heap-floor break, not paginate the whole history — measured by real list-GET
    count (the prior MagicMock-on-one-page test could not see pagination at all).

    Validity: RED if the break is removed from list_recent_merged_meta — the scan
    then walks every page and list_gets jumps to the page count.
    """

    def test_meta_stops_at_heap_floor(self, fake_api) -> None:
        # n=3. The 3 newest-by-merged_at live on pages 1-2; every later page holds
        # PRs whose updated_at is below the heap floor, so the break must fire
        # before page 3 is ever fetched.
        page1 = [
            _summary(101, merged_at=datetime(2024, 5, 25, tzinfo=UTC),
                     updated_at=datetime(2024, 5, 25, tzinfo=UTC)),
            _summary(102, merged_at=datetime(2024, 5, 20, tzinfo=UTC),
                     updated_at=datetime(2024, 5, 20, tzinfo=UTC)),
        ]
        page2 = [
            _summary(103, merged_at=datetime(2024, 5, 15, tzinfo=UTC),
                     updated_at=datetime(2024, 5, 15, tzinfo=UTC)),
            # heap is now full {May25,May20,May15}, floor=May15; this PR's
            # updated_at (Jan 10) < floor → break, pages 3-4 never fetched.
            _summary(104, merged_at=datetime(2024, 1, 10, tzinfo=UTC),
                     updated_at=datetime(2024, 1, 10, tzinfo=UTC)),
        ]
        old = lambda n, d: _summary(  # noqa: E731
            n, merged_at=datetime(2024, 1, d, tzinfo=UTC),
            updated_at=datetime(2024, 1, d, tzinfo=UTC))
        api = fake_api([page1, page2, [old(90, 9), old(89, 8)],
                        [old(80, 7), old(79, 6)]])

        from harness.connectors.github import GitHubConnector

        conn = GitHubConnector(FAKE_TOKEN)
        meta = conn.list_recent_merged_meta("owner/repo", n=3)

        # Correct top-3 by merged_at
        assert {m.number for m in meta} == {101, 102, 103}, "top-3 by merged_at"
        assert min(m.merged_at for m in meta) == datetime(2024, 5, 15, tzinfo=UTC)
        # Bounded to the heap-floor window — NOT all 4 pages.
        assert api.list_gets <= 2, (
            f"meta scan paginated {api.list_gets} pages — it must stop at the "
            "heap-floor window (<=2), not walk the whole history"
        )
        # Metadata-only: no per-PR completion / diff fetch.
        assert api.completion_gets == 0, "meta scan must fetch no per-PR completion"


class TestScopeLowerBoundWindow:
    """The scope lower bound (`since`) — NOT a cursor/overlap window — is what
    decides yield/skip. A PR merged below `since` is never yielded; a PR merged
    at/above `since` is. (overlap_margin was removed: the full scope re-scan +
    present-in-DB probe subsume the old drift re-yield window — gate #1 / BUG C.)
    """

    def test_since_is_the_inclusive_lower_bound(self) -> None:
        since = datetime(2024, 5, 8, tzinfo=UTC)

        # At/above `since` → yielded (merged May 9).
        in_scope = _mock_pr(
            50,
            merged_at=datetime(2024, 5, 9, tzinfo=UTC),
            updated_at=datetime(2024, 5, 20, tzinfo=UTC),
            created_at=datetime(2023, 1, 1, tzinfo=UTC),
        )
        # Below `since` (merged May 7) → never yielded, even though re-touched.
        below_scope = _mock_pr(
            51,
            merged_at=datetime(2024, 5, 7, tzinfo=UTC),
            updated_at=datetime(2024, 5, 21, tzinfo=UTC),
            created_at=datetime(2023, 1, 1, tzinfo=UTC),
        )
        ordered = [below_scope, in_scope]  # updated-desc

        with patch("harness.connectors.github.Github") as MockGithub:
            from harness.connectors.github import GitHubConnector

            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = ordered
            MockGithub.return_value.get_repo.return_value = mock_repo
            conn = GitHubConnector(FAKE_TOKEN)
            nums = [
                r.number
                for r in conn.list_merged_prs("owner/repo", since=since)
            ]

        assert 50 in nums, "PR merged at/above `since` must be yielded"
        assert 51 not in nums, "PR merged below `since` must NOT be yielded"


class TestBotFilterCompletionCost:
    """A bot rejected by is_bot (author — a list-payload field) must cost ZERO
    completion GETs. The completion GET fetches additions/deletions/changed_files,
    needed only by is_giant, which runs AFTER is_bot. So it must be paid only for
    NON-bot PRs. Counted at the real Requester layer via _FakeGitHubAPI (the cost
    is invisible to MagicMock connectors).

    Validity: RED on the current _raw_meta, which reads pr.additions at yield time
    — before the ingester's bot filter — so completion_gets includes the bots.
    """

    def test_bot_costs_no_completion_get(self, fake_api) -> None:
        from harness.connectors.github import GitHubConnector
        from harness.ingester.ingest import Ingester

        def merged(n: int, author: str) -> dict:
            d = datetime(2024, 5, (n % 27) + 1, tzinfo=UTC)
            return _summary(n, merged_at=d, updated_at=d, author=author)

        # 2 bots + 3 non-bots. Non-bots are GIANT (detailed changed_files=200) so
        # is_giant filters them BEFORE fetch_diff — isolating the size() completion
        # cost from fetch_diff's own GET.
        page1 = [merged(1, "dependabot[bot]"), merged(2, "alice"),
                 merged(3, "renovate[bot]")]
        page2 = [merged(4, "bob"), merged(5, "carol")]
        api = fake_api([page1, page2], detailed_changed_files=200)

        connector = GitHubConnector(FAKE_TOKEN)
        # Sidestep the /rate_limit GET — not what this test counts.
        connector.rate_limit_status = lambda: RateLimitStatus(
            remaining=5000, reset_at=datetime(2030, 1, 1, tzinfo=UTC), limit=5000
        )

        mock_conn = MagicMock()
        with patch("harness.ingester.ingest.PRRepo") as MockPRRepo, patch(
            "harness.ingester.ingest.RepositoryRepo"
        ) as MockRepoRepo:
            # Probe runs BEFORE size(): a "missing" PR must reach size() (its
            # completion GET); were the probe to report present, size() would be
            # skipped and this test could not observe the per-non-bot GET.
            MockPRRepo.return_value.exists.return_value = False
            MockRepoRepo.return_value.upsert.return_value = MagicMock(id=1)
            MockRepoRepo.return_value.get_op_state.return_value = None
            Ingester(mock_conn).run(connector, "owner/repo", "proj")

        non_bots = 3  # alice, bob, carol
        assert api.completion_gets == non_bots, (
            f"expected {non_bots} completion GETs (one per non-bot, for the giant "
            f"check), got {api.completion_gets} — a bot rejected by author must "
            "cost ZERO completion GETs"
        )
