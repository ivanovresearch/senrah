"""
harness.ingester.filters — pure bot/giant filter predicates (INGEST-03).

Module-level helpers: no I/O, no DB, no network.
Follows the pure-helper convention established by extract_linked_issue
in connectors/base.py.

Design:
- is_bot: author ending in [bot] suffix OR present in configurable stop-list
- is_giant: files_changed > max_files OR (additions + deletions) > max_lines
  Boundaries are STRICTLY GREATER (not >=) per spec.

These are called on cheap RawPR metadata BEFORE the diff fetch to avoid
burning rate limit on PRs that will be excluded (Pattern 2 / Pitfall 10).
"""

from __future__ import annotations


def is_bot(author: str, stop_list: frozenset[str]) -> bool:
    """Return True if the author is a bot.

    A PR author is considered a bot if:
    - Their login ends with the "[bot]" suffix (GitHub Apps convention), OR
    - They are present in the configurable stop_list (case-sensitive).

    Examples:
        >>> is_bot("dependabot[bot]", frozenset())
        True
        >>> is_bot("renovate[bot]", frozenset())
        True
        >>> is_bot("alice", frozenset({"alice"}))
        True
        >>> is_bot("alice", frozenset())
        False
    """
    return author.endswith("[bot]") or author in stop_list


def is_giant(
    files_changed: int,
    additions: int,
    deletions: int,
    max_files: int = 100,
    max_lines: int = 5000,
) -> bool:
    """Return True if the PR is a 'giant' PR that should be excluded from ingest.

    A PR is giant if:
    - files_changed > max_files (strictly greater — 100 files is NOT giant), OR
    - additions + deletions > max_lines (strictly greater — 5000 lines is NOT giant)

    Uses the cheap integer counts available on the PyGithub PR object (pr.changed_files,
    pr.additions, pr.deletions) — no get_files() pagination needed.

    Examples:
        >>> is_giant(101, 0, 0)
        True
        >>> is_giant(100, 0, 0)   # boundary: exactly 100 is NOT giant
        False
        >>> is_giant(0, 3000, 3000)  # 6000 lines > 5000
        True
        >>> is_giant(0, 2500, 2500)  # exactly 5000 lines is NOT giant
        False
    """
    return files_changed > max_files or (additions + deletions) > max_lines
