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
"""

from __future__ import annotations

import logging
import sys
from typing import Iterator

import httpx
from github import Github, GithubRetry
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from harness.connectors.base import (
    ConnectorProtocol,
    PRCursor,
    RateLimitStatus,
    RawPR,
    extract_linked_issue,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry decorator for raw diff fetch via httpx
# (GithubRetry handles PyGithub calls; httpx calls need separate retry)
# ---------------------------------------------------------------------------

_diff_retry = retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    stop=stop_after_attempt(4),
    reraise=True,
)


class GitHubConnector:
    """GitHub connector implementing ConnectorProtocol (structural subtyping).

    Constructor takes a token; uses Github(token, retry=GithubRetry()) for PR
    listing/metadata and an httpx.Client with the diff Accept header for raw
    diff fetch.

    Phase 1 limitations (all deferred to Phase 3 per D-04):
    - cursor parameter is accepted but ignored (since_date not implemented)
    - No full backoff strategy (basic tenacity retry only)
    - No token scope validation (T-02-05: surfaces auth errors at first API call)
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

    def validate_credentials(self) -> None:
        """Verify that the token can authenticate against GitHub.

        Raises github.GithubException (or subclass) on invalid/missing token.
        Phase 3 (OPS-01) adds full scope validation.
        """
        user = self._g.get_user()
        _ = user.login  # raises github.GithubException on invalid token

    def list_merged_prs(
        self,
        repo_full_name: str,
        last_n: int | None = None,
        cursor: PRCursor | None = None,  # accepted, ignored in Phase 1 (D-04)
    ) -> Iterator[RawPR]:
        """Yield merged PRs for the given repository, newest first.

        Iterates get_pulls(state="closed", sort="updated", direction="desc"),
        skips unmerged PRs, fetches the diff via httpx, and yields RawPR.

        Args:
            repo_full_name: "owner/repo" string (e.g. "dotnet/runtime").
            last_n: Stop after yielding this many merged PRs.  None = no limit.
            cursor: Ignored in Phase 1; Phase 3 adds since_date filtering.
        """
        repo = self._g.get_repo(repo_full_name)
        count = 0

        for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
            if pr.merged_at is None:
                continue  # skip unmerged (closed but not merged) PRs

            diff_text = self._fetch_diff(pr.diff_url)

            yield RawPR(
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
            )

            count += 1
            if last_n is not None and count >= last_n:
                break

    def fetch_pr(self, repo_full_name: str, number: int) -> RawPR:
        """Fetch a single PR by number."""
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
        """Fetch raw diff text from diff_url with tenacity retry.

        The URL is sourced from pr.diff_url from the authenticated GitHub API
        (not user-supplied — T-02-03 SSRF mitigation).
        Raises httpx.HTTPStatusError or httpx.TransportError on persistent failure.
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
    connector: ConnectorProtocol = GitHubConnector("fake-token-for-type-check")
    _ = connector  # suppress unused-variable warning
