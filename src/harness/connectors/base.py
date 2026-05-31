"""
harness.connectors.base — ConnectorProtocol, RawPR, PRCursor, RateLimitStatus,
and extract_linked_issue.

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
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Protocol


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawPR:
    """A merged pull request as returned by a connector.

    Carries all metadata needed for the Ingester to write to pull_requests
    without further API calls.
    """

    number: int
    title: str
    body: str  # PR description (may be empty)
    diff: str  # raw diff text
    author: str
    merged_at: datetime
    repo_full_name: str
    linked_issue: str | None  # extracted from body via Closes/Fixes regex
    files_changed: list[str]
    additions: int
    deletions: int


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

    def validate_credentials(self) -> None:
        """Raise an exception if credentials are missing or invalid.

        Phase 1: verifies that the token can authenticate.
        Phase 3 (OPS-01) adds scope validation.
        """
        ...

    def list_merged_prs(
        self,
        repo_full_name: str,
        last_n: int | None = None,
        cursor: PRCursor | None = None,
    ) -> Iterator[RawPR]:
        """Yield merged PRs for the given repository.

        Args:
            repo_full_name: "owner/repo" string.
            last_n: Stop after yielding this many PRs.  None means no limit.
            cursor: Resume from this cursor position.  Ignored in Phase 1;
                    Phase 3 adds since_date filtering (D-04).
        """
        ...

    def fetch_pr(self, repo_full_name: str, number: int) -> RawPR:
        """Fetch a single PR by number."""
        ...

    def rate_limit_status(self) -> RateLimitStatus:
        """Return the current rate-limit status for the authenticated user."""
        ...
