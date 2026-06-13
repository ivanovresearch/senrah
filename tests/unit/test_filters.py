"""
Unit tests for senrah.ingester.filters — is_bot / is_giant pure predicates.

Covers INGEST-03:
- is_bot: author ending in [bot] suffix or present in stop-list
- is_giant: files_changed > 100 OR (additions + deletions) > 5000
  Boundaries are STRICTLY GREATER (not >=).

No I/O, no network, no mocks needed — these are pure-function tests.
"""

from __future__ import annotations

import pytest

from senrah.ingester.filters import is_bot, is_giant


class TestIsBot:
    def test_bot_suffix_is_true(self) -> None:
        assert is_bot("dependabot[bot]", frozenset()) is True

    def test_bot_suffix_any_prefix(self) -> None:
        assert is_bot("renovate[bot]", frozenset()) is True

    def test_stop_list_match_is_true(self) -> None:
        assert is_bot("alice", frozenset({"alice"})) is True

    def test_normal_user_no_stop_list(self) -> None:
        assert is_bot("alice", frozenset()) is False

    def test_empty_string_not_bot(self) -> None:
        assert is_bot("", frozenset()) is False

    def test_stop_list_case_sensitive(self) -> None:
        """Stop-list membership is case-sensitive."""
        assert is_bot("Alice", frozenset({"alice"})) is False

    def test_bot_suffix_not_in_middle(self) -> None:
        """[bot] in middle of string is not matched by endswith."""
        assert is_bot("[bot]user", frozenset()) is False

    def test_multiple_stop_list_entries(self) -> None:
        assert is_bot("github-actions", frozenset({"github-actions", "snyk-bot"})) is True


class TestIsGiant:
    def test_files_over_limit_is_true(self) -> None:
        assert is_giant(101, 0, 0) is True

    def test_files_at_limit_is_false(self) -> None:
        """Boundary: exactly 100 files is NOT giant (strictly greater)."""
        assert is_giant(100, 0, 0) is False

    def test_lines_over_limit_is_true(self) -> None:
        """additions + deletions > 5000 triggers giant."""
        assert is_giant(0, 3000, 3000) is True  # 6000 total > 5000

    def test_lines_at_limit_is_false(self) -> None:
        """Boundary: exactly 5000 lines is NOT giant (strictly greater)."""
        assert is_giant(100, 0, 5000) is False  # 5000 total, 100 files — neither exceeds

    def test_lines_exact_boundary(self) -> None:
        """additions + deletions == 5000 is not giant."""
        assert is_giant(0, 2500, 2500) is False

    def test_lines_one_over_boundary(self) -> None:
        """additions + deletions == 5001 is giant."""
        assert is_giant(0, 2501, 2500) is True

    def test_files_one_over_boundary(self) -> None:
        assert is_giant(101, 0, 0) is True

    def test_both_under_limit(self) -> None:
        assert is_giant(50, 1000, 500) is False

    def test_custom_max_files(self) -> None:
        """Custom max_files threshold."""
        assert is_giant(51, 0, 0, max_files=50) is True
        assert is_giant(50, 0, 0, max_files=50) is False

    def test_custom_max_lines(self) -> None:
        """Custom max_lines threshold."""
        assert is_giant(0, 1001, 0, max_lines=1000) is True
        assert is_giant(0, 1000, 0, max_lines=1000) is False
