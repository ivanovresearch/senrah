"""
harness.connectors.base — ConnectorProtocol, RawPR, PRCursor, PRMeta,
RateLimitStatus, and extract_linked_issue.

The ConnectorProtocol is the core extensibility seam:
- Defined as typing.Protocol (structural subtyping, NOT an ABC)
- A new VCS source is added by implementing these four methods; the Ingester
  never needs to change (INGEST-01 / STATE.md decision)
- The connector MUST NOT import anything from db/, indexer/, or ingester/

Design decisions:
- typing.Protocol: zero-overhead structural typing; no base class import needed
  (STATE.md decision: "ConnectorProtocol as typing.Protocol")
- Frozen dataclasses: immutable value objects for thread safety and correctness
- extract_linked_issue: minimal Phase 1 regex; case-insensitive for closes/fixes/resolves
- RawPR.diff is str | None: None during traversal (diff-less yield); fetched
  only for survivors via fetch_diff() (INGEST-03 pre-fetch filter guarantee)
- PRMeta: lightweight (number, merged_at) for the newest-N window provider
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator, Protocol

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawPR:
    """A merged pull request as returned by a connector.

    Carries all metadata needed for the Ingester to write to pull_requests
    without further API calls.

    diff is None during traversal (list_merged_prs yields cheap metadata only).
    The diff is fetched for surviving PRs via fetch_diff() after bot/giant
    filtering — this structurally guarantees no diff is fetched for excluded PRs
    (INGEST-03 pre-fetch filter guarantee).
    """

    number: int
    title: str
    body: str  # PR description (may be empty)
    diff: str | None  # None until fetched via fetch_diff(); raw diff text after
    author: str
    merged_at: datetime
    repo_full_name: str
    linked_issue: str | None  # extracted from body via Closes/Fixes regex
    files_changed: list[str]
    additions: int
    deletions: int
    # Cheap changed-file COUNT from the PR metadata (pr.changed_files). Carried
    # separately from files_changed because the diff-less traversal yields the
    # count (one int) without the file list (get_files() would paginate). The
    # Ingester's giant-PR filter (INGEST-03) reads this; files_changed stays []
    # at traversal time.
    changed_files: int = 0


@dataclass(frozen=True)
class PRCursor:
    """Pagination cursor for list_merged_prs.

    Phase 1: cursor is accepted but ignored (since_date is Phase 3 per D-04).
    The tiebreak field is included now so Phase 3 can use it without a dataclass
    change.
    """

    merged_at: datetime
    number: int  # tiebreak for same-second merges


@dataclass(frozen=True)
class PRMeta:
    """Lightweight PR metadata for the newest-N window-lower-bound provider.

    Returned by list_recent_merged_meta(); used by the Ingester to compute
    the last_n window lower-bound via resolve_since(scope, last_n_merged_at_provider=...).
    """

    number: int
    merged_at: datetime


@dataclass(frozen=True)
class RateLimitStatus:
    """GitHub (or other VCS) rate-limit state."""

    remaining: int
    reset_at: datetime
    limit: int


# ---------------------------------------------------------------------------
# Linked-issue extraction (RESEARCH Pattern 3)
# ---------------------------------------------------------------------------

_LINKED_ISSUE_RE = re.compile(
    r"(?:clos(?:es?|e)|fix(?:es?)?|resolv(?:es?|e))\s+#(\d+)",
    re.IGNORECASE,
)


def extract_linked_issue(body: str) -> str | None:
    """Return the first linked issue reference (e.g. '#123') from a PR body.

    Recognises the keywords closes/close, fixes/fix, resolves/resolve
    followed by '#<number>'.  Case-insensitive.  Returns None when no
    reference is found.

    Examples:
        extract_linked_issue("Closes #123")  → "#123"
        extract_linked_issue("fixes #42")    → "#42"
        extract_linked_issue("No issue")     → None
    """
    match = _LINKED_ISSUE_RE.search(body or "")
    return f"#{match.group(1)}" if match else None


# ---------------------------------------------------------------------------
# ConnectorProtocol (typing.Protocol — structural subtyping)
# ---------------------------------------------------------------------------


class ConnectorProtocol(Protocol):
    """Interface for VCS connectors.

    Any class that implements these four methods structurally satisfies the
    interface — no import or inheritance of this class is required.

    Boundary constraint: implementations MUST NOT import harness.db,
    harness.indexer, or harness.ingester (connectors know nothing about DB
    schema or embeddings).
    """

    def validate_credentials(self, repo_full_name: str | None = None) -> None:
        """Raise an exception if credentials are missing or invalid.

        Phase 1: verifies that the token can authenticate.
        Phase 3 (OPS-01): when repo_full_name is given, also performs a live
        test-read on the target repo and raises a token-free message on 403/404.
        """
        ...

    def list_merged_prs(
        self,
        repo_full_name: str,
        *,
        since: datetime | None = None,
        cursor: PRCursor | None = None,
        last_n: int | None = None,
        overlap_margin: timedelta | None = None,
    ) -> Iterator[RawPR]:
        """Yield merged PRs for the given repository.

        Two modes, selected by whether a cursor is supplied (RESEARCH Pattern 1,
        Design B — supersedes the created-asc full-scan accepted "at MVP"):

        - Backfill (cursor is None): created-ascending forward spine. Stable and
          fully-paginable; the correct one-time enumeration of repo history.
        - Incremental (cursor set): updated-descending scan that BREAKS as soon as
          updated_at < (cursor.merged_at - overlap_margin). A merge bumps
          updated_at (updated_at >= merged_at), so no PR merged after the cursor
          is missed, and the scan stops at the cursor window instead of walking
          the whole history every run. Yields merged PRs with
          merged_at > (cursor.merged_at - overlap_margin); the overlap window is
          re-yielded so any PR transiently skipped by updated-order pagination
          drift is recovered next run (idempotent upsert dedups). Residual hole:
          a merge whose visibility lags the cursor by more than overlap_margin can
          still be missed — documented in RESEARCH Pattern 1.

        Yields cheap PR metadata with diff=None — no diff is fetched during
        traversal; the diff is fetched only for survivors via fetch_diff().

        Args:
            repo_full_name: "owner/repo" string.
            since: Scope-window lower bound; skip PRs with merged_at < since.
            cursor: When given, selects incremental mode and supplies the
                    merged_at high-water mark.
            last_n: Stop after yielding this many merged PRs.  None = no limit.
            overlap_margin: Re-yield/break safety window for incremental mode
                    (drift defence). Policy (derive from prior run duration) lives
                    in the Ingester; the connector only applies it. None = 0.
        """
        ...

    def fetch_diff(self, repo_full_name: str, number: int) -> str:
        """Fetch the raw diff text for a single PR by number.

        This is the ONLY place diffs are fetched — called by the Ingester for
        PRs that survive bot/giant filtering (INGEST-03 structurally guaranteed).
        """
        ...

    def fetch_pr(self, repo_full_name: str, number: int) -> RawPR:
        """Fetch a single PR by number."""
        ...

    def list_recent_merged_meta(
        self, repo_full_name: str, n: int
    ) -> list[PRMeta]:
        """Return the newest N merged PRs by merged_at (number, merged_at).

        Metadata-only scan — no diffs fetched. Because no endpoint orders by
        merged_at, this scans updated-descending and keeps the top-N by merged_at
        in a bounded heap, stopping once updated_at drops below the smallest
        merged_at already held (merged_at <= updated_at, so nothing later can
        enter the top-N). This returns the true newest-N by merge time rather than
        the created-order proxy. Used by the Ingester to compute the last_n window
        lower-bound: since = min(result[i].merged_at), passed to
        list_merged_prs(since=...).
        """
        ...

    def rate_limit_status(self) -> RateLimitStatus:
        """Return the current rate-limit status for the authenticated user."""
        ...
