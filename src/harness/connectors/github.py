"""
harness.connectors.github — GitHubConnector implementing ConnectorProtocol.

Uses:
- PyGithub (Github + GithubRetry) for PR listing, metadata, and rate-limit status
- httpx with Accept: application/vnd.github.v3.diff for raw diff fetch
  (PyGithub cannot return raw diff content — RESEARCH Pattern 4)
- tenacity for retrying the httpx diff fetch (GithubRetry covers only PyGithub
  calls; the raw diff fetch does not go through GithubRetry — RESEARCH rate-limit note)

Boundary constraint: this module MUST NOT import harness.db, harness.indexer,
or harness.ingester.  The connector knows nothing about DB schema or embeddings.

Security:
- T-02-01: Token read from ENV at the composition root (cli/ingest.py); never
  logged here; tests use a fake token literal.
- T-02-03: Diff URL is sourced from pr.diff_url returned by the authenticated
  GitHub API (not from user input); httpx timeout set; follow_redirects scoped.
- T-OPS01-1: validate_credentials raises a token-free message on 403/404;
  the token literal is never included in any error message or log line.
"""

from __future__ import annotations

import heapq
import logging
from datetime import datetime, timedelta
from typing import Iterator

import httpx
from github import Github, GithubException, GithubRetry
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from harness.connectors.base import (
    PRCursor,
    PRMeta,
    RateLimitStatus,
    RawPR,
    extract_linked_issue,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry-After-aware wait callable for the httpx diff fetch retry
# (GithubRetry handles PyGithub calls; httpx calls need separate retry)
# RESEARCH Pattern 5: honor Retry-After header on 403 (secondary rate limit)
# and 429 (primary rate limit) before falling back to capped exponential.
# ---------------------------------------------------------------------------


def _retry_after_wait(retry_state):  # type: ignore[no-untyped-def]
    """Tenacity wait callable that honors the Retry-After response header.

    If the exception is an httpx.HTTPStatusError and the response carries a
    numeric Retry-After header, return that value (in seconds) so tenacity
    waits exactly as long as GitHub requests.  Otherwise fall back to capped
    exponential backoff.
    """
    exc = retry_state.outcome.exception()
    if isinstance(exc, httpx.HTTPStatusError):
        ra = exc.response.headers.get("Retry-After")
        if ra and ra.isdigit():
            return float(ra)  # honor server-specified backoff (403/429)
    # Fall back to capped exponential for transport errors / no Retry-After header
    return wait_exponential(multiplier=1, min=1, max=16)(retry_state)


_diff_retry = retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
    wait=_retry_after_wait,
    stop=stop_after_attempt(5),
    reraise=True,
)


class GitHubConnector:
    """GitHub connector implementing ConnectorProtocol (structural subtyping).

    Constructor takes a token; uses Github(token, retry=GithubRetry()) for PR
    listing/metadata and an httpx.Client with the diff Accept header for raw
    diff fetch.

    Traversal design:
    - list_merged_prs uses created-ascending spine (INGEST-05 Pattern 1):
      stable, fully-paginable enumeration; skips unmerged and pre-window PRs;
      yields cheap metadata (diff=None) for pre-fetch filter (INGEST-03).
    - fetch_diff is the single survivor-only diff path; called by the Ingester
      only for PRs that survive bot/giant filtering.
    - list_recent_merged_meta provides the newest-N window lower-bound for
      last_n scope resolution (RESEARCH Pattern 3).
    """

    def __init__(self, token: str) -> None:
        self._token = token
        # GithubRetry handles 403 Retry-After (secondary rate limits) and 429
        self._g = Github(token, retry=GithubRetry())
        # httpx client for raw diff fetch — requires specific Accept header
        self._http = httpx.Client(
            headers={
                "Accept": "application/vnd.github.v3.diff",
                "Authorization": f"token {token}",
            },
            follow_redirects=True,
            timeout=15.0,  # per-request timeout; giant PRs can be slow (T-02-03)
        )

    def validate_credentials(self, repo_full_name: str | None = None) -> None:
        """Verify that the token can authenticate against GitHub.

        Auth check: verifies user.login (raises GithubException on invalid token).
        Scope check (advisory): reads oauth_scopes header for classic PATs only;
        never rejects on missing scopes (fine-grained PATs always report None).
        Test-read: when repo_full_name is given, does a live test-read
        (get_pulls(state="closed") + force one page) so fine-grained tokens
        are validated correctly regardless of oauth_scopes.

        Raises a token-free exception on 403/404 (OPS-01 — T-OPS01-1).
        The token literal is NEVER included in any error message or log line.
        """
        user = self._g.get_user()
        _ = user.login  # raises GithubException on invalid/missing token

        # Classic-PAT advisory check (None for fine-grained PATs — do not reject)
        scopes = getattr(self._g, "oauth_scopes", None)  # noqa: F841 (advisory only)

        if repo_full_name:
            try:
                repo = self._g.get_repo(repo_full_name)
                prs = repo.get_pulls(state="closed")
                _ = prs[0:1]  # force one API page → 403 if no PR read access
            except GithubException as exc:
                if exc.status in (403, 404):
                    raise RuntimeError(
                        f"Token cannot read pull requests on {repo_full_name!r}. "
                        "Grant read-only Pull requests + Issues access and retry."
                    ) from None
                raise

    def list_merged_prs(
        self,
        repo_full_name: str,
        *,
        since: datetime | None = None,
        cursor: PRCursor | None = None,
        last_n: int | None = None,
        overlap_margin: timedelta | None = None,
    ) -> Iterator[RawPR]:
        """Yield merged PRs for the given repository (diff=None — cheap metadata).

        Two modes (RESEARCH Pattern 1, Design B):
        - Backfill (cursor is None): created-ascending forward spine. Stable,
          fully-paginable; the correct one-time enumeration of repo history.
        - Incremental (cursor set): updated-descending scan with an early break at
          the cursor window — does NOT re-walk the whole history every run.

        See ConnectorProtocol.list_merged_prs for the full contract.
        """
        repo = self._g.get_repo(repo_full_name)
        if cursor is None:
            yield from self._backfill_created_asc(repo, repo_full_name, since, last_n)
        else:
            yield from self._incremental_updated_desc(
                repo, repo_full_name, cursor, since, last_n, overlap_margin
            )

    def _raw_meta(self, pr, repo_full_name: str) -> RawPR:  # type: ignore[no-untyped-def]
        """Build a diff-less RawPR from a PR's CHEAP list-payload metadata only.

        Reads only fields present in the PR list payload (number/title/body/
        author/merged_at) — NO completion GET fires here. The giant-filter fields
        (changed_files/additions/deletions) live only in the per-PR completion
        payload (GET /pulls/{n}); reading them eagerly would charge that GET for
        every yielded PR, including bots that is_bot rejects for free. They are
        therefore deferred to RawPR.size() via size_loader, which the Ingester
        calls only AFTER is_bot (Finding 2 — N+1 paid past the bot filter).
        pr.get_files() would paginate and is avoided.
        """
        # Bind pr in the closure default so it captures THIS PR (not a later
        # loop rebinding). The completion GET fires on first attribute read.
        return RawPR(
            number=pr.number,
            title=pr.title,
            body=pr.body or "",
            diff=None,  # deferred to fetch_diff() for survivors
            author=pr.user.login,
            merged_at=pr.merged_at,
            repo_full_name=repo_full_name,
            linked_issue=extract_linked_issue(pr.body or ""),
            files_changed=[],  # list not fetched at traversal
            size_loader=lambda p=pr: (p.changed_files, p.additions, p.deletions),
        )

    def _backfill_created_asc(
        self, repo, repo_full_name: str, since: datetime | None, last_n: int | None
    ) -> Iterator[RawPR]:  # type: ignore[no-untyped-def]
        """First-run enumeration: created-asc forward spine (no cursor)."""
        count = 0
        # created-asc = stable forward spine; fully paginable (no 1000 cap)
        for pr in repo.get_pulls(state="closed", sort="created", direction="asc"):
            if pr.merged_at is None:
                continue  # skip unmerged (closed but not merged) PRs
            if since is not None and pr.merged_at < since:
                continue  # below the scope window lower bound
            yield self._raw_meta(pr, repo_full_name)
            count += 1
            if last_n is not None and count >= last_n:
                break

    def _incremental_updated_desc(
        self,
        repo,
        repo_full_name: str,
        cursor: PRCursor,
        since: datetime | None,
        last_n: int | None,
        overlap_margin: timedelta | None,
    ) -> Iterator[RawPR]:  # type: ignore[no-untyped-def]
        """Steady-state catch-up: updated-desc scan with early break (Design B).

        Invariant: a merge bumps updated_at, so updated_at >= merged_at. Any PR
        merged after the cursor therefore has updated_at > bound and is visited
        before the break. The scan stops once updated_at < bound instead of
        paginating the whole history.

        bound = cursor.merged_at - overlap_margin. The (bound, cursor] overlap is
        re-yielded so a PR transiently skipped by updated-order pagination drift is
        recovered next run (idempotent upsert dedups). Residual hole: a merge whose
        visibility lags by more than overlap_margin can still be missed.
        """
        margin = overlap_margin or timedelta(0)
        bound = cursor.merged_at - margin
        # Clamp the re-yield window to the scope lower bound when one is given
        # (forward-only: cursor >= since, so this only trims the overlap tail).
        lower = bound if since is None else max(bound, since)
        count = 0
        for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
            updated_at = pr.updated_at
            if updated_at is not None and updated_at < bound:
                break  # updated-desc: nothing past here can be a merge above cursor
            if pr.merged_at is None:
                continue  # updated for a non-merge reason (comment/label) or open
            if pr.merged_at <= lower:
                continue  # at/below the high-water mark — already ingested
            yield self._raw_meta(pr, repo_full_name)
            count += 1
            if last_n is not None and count >= last_n:
                break

    def fetch_diff(self, repo_full_name: str, number: int) -> str:
        """Fetch the raw diff text for a single PR by number.

        This is the ONLY place diffs are fetched — called by the Ingester for
        PRs that survive bot/giant filtering (INGEST-03 structurally guaranteed).
        Uses _diff_retry with Retry-After-aware backoff (INGEST-06, T-INGEST06-1).
        """
        repo = self._g.get_repo(repo_full_name)
        pr = repo.get_pull(number)
        return self._fetch_diff(pr.diff_url)

    def list_recent_merged_meta(
        self, repo_full_name: str, n: int
    ) -> list[PRMeta]:
        """Return the newest N merged PRs by merged_at (number, merged_at).

        No endpoint orders by merged_at, so scan updated-descending and keep the
        top-N by merged_at in a bounded min-heap. Stop once the heap is full and
        the current PR's updated_at is below the smallest merged_at held: since
        merged_at <= updated_at and updated_at is non-increasing, nothing later
        can enter the top-N. This returns the true newest-N by merge time, not the
        created-order proxy. Metadata-only — no diffs fetched. The Ingester uses
        min(result[i].merged_at) as the last_n window lower-bound (INGEST-04).

        Args:
            repo_full_name: "owner/repo" string.
            n: Maximum number of merged PRs to return.
        """
        if n <= 0:
            return []

        repo = self._g.get_repo(repo_full_name)
        # Min-heap of (merged_at, number): heap[0] is the smallest merged_at held.
        heap: list[tuple[datetime, int]] = []

        for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
            updated_at = pr.updated_at
            if (
                len(heap) >= n
                and updated_at is not None
                and updated_at < heap[0][0]
            ):
                break  # no later PR's merged_at can beat the current top-N floor

            if pr.merged_at is None:
                continue  # skip unmerged closed PRs

            if len(heap) < n:
                heapq.heappush(heap, (pr.merged_at, pr.number))
            elif pr.merged_at > heap[0][0]:
                heapq.heapreplace(heap, (pr.merged_at, pr.number))

        # Newest merged first
        return [
            PRMeta(number=number, merged_at=merged_at)
            for merged_at, number in sorted(heap, reverse=True)
        ]

    def fetch_pr(self, repo_full_name: str, number: int) -> RawPR:
        """Fetch a single PR by number (including diff)."""
        repo = self._g.get_repo(repo_full_name)
        pr = repo.get_pull(number)
        diff_text = self._fetch_diff(pr.diff_url)
        return RawPR(
            number=pr.number,
            title=pr.title,
            body=pr.body or "",
            diff=diff_text,
            author=pr.user.login,
            merged_at=pr.merged_at,
            repo_full_name=repo_full_name,
            linked_issue=extract_linked_issue(pr.body or ""),
            files_changed=[f.filename for f in pr.get_files()],
            additions=pr.additions,
            deletions=pr.deletions,
            changed_files=pr.changed_files,
        )

    def rate_limit_status(self) -> RateLimitStatus:
        """Return the current rate-limit status for the authenticated user."""
        rl = self._g.get_rate_limit().core
        return RateLimitStatus(
            remaining=rl.remaining,
            reset_at=rl.reset,
            limit=rl.limit,
        )

    @_diff_retry
    def _fetch_diff(self, diff_url: str) -> str:
        """Fetch raw diff text from diff_url with Retry-After-aware retry.

        The URL is sourced from pr.diff_url from the authenticated GitHub API
        (not user-supplied — T-02-03 SSRF mitigation).
        Raises httpx.HTTPStatusError or httpx.TransportError on persistent failure.
        The _retry_after_wait callable reads Retry-After from the response
        before falling back to capped exponential (INGEST-06, T-INGEST06-1).
        """
        response = self._http.get(diff_url)
        response.raise_for_status()
        return response.text


# ---------------------------------------------------------------------------
# Structural conformance smoke check (for mypy — INGEST-01)
# ---------------------------------------------------------------------------

def _check_structural_conformance() -> None:
    """Type-check-only: verify GitHubConnector satisfies ConnectorProtocol.

    This function is never called at runtime; it exists so mypy can verify
    that GitHubConnector is a structural subtype of ConnectorProtocol
    without a concrete import of the base class in the Ingester.
    """
    from harness.connectors.base import ConnectorProtocol
    connector: ConnectorProtocol = GitHubConnector("fake-token-for-type-check")
    _ = connector  # suppress unused-variable warning
