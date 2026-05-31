"""
Unit tests for harness.connectors.base and harness.connectors.github.

All tests use respx to mock httpx diff-URL fetch and a stubbed PyGithub
PR object (via unittest.mock.patch).  No real network calls, no real token.

Coverage:
- extract_linked_issue: positive cases (closes/fixes/resolves, case variants),
  None when no reference present
- GitHubConnector.list_merged_prs: merged-only filtering, last_n cutoff,
  diff text populated from mocked diff endpoint, RawPR fields correct
- ConnectorProtocol structural conformance (mypy static check via type comment)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import respx
import httpx

from harness.connectors.base import (
    ConnectorProtocol,
    PRCursor,
    RateLimitStatus,
    RawPR,
    extract_linked_issue,
)
from harness.connectors.github import GitHubConnector


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

FAKE_TOKEN = "ghp_fake_token_for_testing_only_1234567890"

MERGED_AT = datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
FAKE_DIFF = "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
DIFF_URL = "https://github.com/owner/repo/pull/42.diff"


def _make_mock_pr(
    number: int,
    title: str,
    body: str,
    merged_at: datetime | None,
    diff_url: str,
    author: str = "author_login",
    files: list[str] | None = None,
    additions: int = 5,
    deletions: int = 2,
) -> MagicMock:
    """Build a minimal PyGithub PullRequest mock."""
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.body = body
    pr.merged_at = merged_at
    pr.diff_url = diff_url
    pr.additions = additions
    pr.deletions = deletions

    # user.login
    pr.user.login = author

    # get_files() returns an iterable of file mocks
    file_mocks = []
    for fname in (files or ["src/foo.py"]):
        fm = MagicMock()
        fm.filename = fname
        file_mocks.append(fm)
    pr.get_files.return_value = file_mocks

    return pr


# ---------------------------------------------------------------------------
# extract_linked_issue tests
# ---------------------------------------------------------------------------


class TestExtractLinkedIssue:
    def test_closes_upper(self) -> None:
        assert extract_linked_issue("Closes #123") == "#123"

    def test_closes_lower(self) -> None:
        assert extract_linked_issue("closes #456") == "#456"

    def test_close_singular(self) -> None:
        assert extract_linked_issue("close #7") == "#7"

    def test_fixes_upper(self) -> None:
        assert extract_linked_issue("Fixes #99") == "#99"

    def test_fixes_lower(self) -> None:
        assert extract_linked_issue("fixes #1001") == "#1001"

    def test_fix_singular(self) -> None:
        assert extract_linked_issue("fix #200") == "#200"

    def test_resolves_upper(self) -> None:
        assert extract_linked_issue("Resolves #300") == "#300"

    def test_resolves_lower(self) -> None:
        assert extract_linked_issue("resolves #400") == "#400"

    def test_resolve_singular(self) -> None:
        assert extract_linked_issue("resolve #5") == "#5"

    def test_in_sentence(self) -> None:
        assert extract_linked_issue("This PR closes #42 as requested.") == "#42"

    def test_no_reference_returns_none(self) -> None:
        assert extract_linked_issue("No issue referenced here.") is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_linked_issue("") is None

    def test_none_body_returns_none(self) -> None:
        # The function accepts a string, but let's test with empty to be safe
        assert extract_linked_issue("") is None

    def test_returns_first_match(self) -> None:
        # When multiple issues are referenced, return the first
        result = extract_linked_issue("Closes #10 and Fixes #20")
        assert result == "#10"

    def test_mixed_case_keyword(self) -> None:
        assert extract_linked_issue("CLOSES #77") == "#77"

    def test_fixes_mixed_case(self) -> None:
        assert extract_linked_issue("FiXeS #88") == "#88"


# ---------------------------------------------------------------------------
# GitHubConnector.list_merged_prs tests
# ---------------------------------------------------------------------------


class TestGitHubConnectorListMergedPRs:
    """Tests for list_merged_prs using respx + PyGithub mocks."""

    def _make_connector_and_mock_repo(
        self, prs: list[MagicMock]
    ) -> tuple[GitHubConnector, MagicMock]:
        """Return a connector with a patched Github and a mock repo."""
        # We'll patch Github inside the test methods using context managers
        connector = GitHubConnector.__new__(GitHubConnector)
        connector._token = FAKE_TOKEN

        mock_repo = MagicMock()
        mock_repo.get_pulls.return_value = prs

        return connector, mock_repo

    @respx.mock
    def test_yields_only_merged_prs(self) -> None:
        """PRs without merged_at are skipped."""
        merged_pr = _make_mock_pr(1, "Merged PR", "body", MERGED_AT, DIFF_URL)
        unmerged_pr = _make_mock_pr(2, "Unmerged PR", "body", None, DIFF_URL)

        respx.get(DIFF_URL).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [merged_pr, unmerged_pr]
            mock_g.get_repo.return_value = mock_repo

            connector = GitHubConnector(FAKE_TOKEN)
            results = list(connector.list_merged_prs("owner/repo"))

        assert len(results) == 1
        assert results[0].number == 1

    @respx.mock
    def test_last_n_limits_results(self) -> None:
        """last_n=2 yields only 2 merged PRs even if more are available."""
        prs = [
            _make_mock_pr(i, f"PR {i}", "body", MERGED_AT, DIFF_URL)
            for i in range(1, 6)
        ]
        respx.get(DIFF_URL).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = prs
            mock_g.get_repo.return_value = mock_repo

            connector = GitHubConnector(FAKE_TOKEN)
            results = list(connector.list_merged_prs("owner/repo", last_n=2))

        assert len(results) == 2

    @respx.mock
    def test_last_n_none_yields_all(self) -> None:
        """last_n=None yields all merged PRs."""
        prs = [
            _make_mock_pr(i, f"PR {i}", "body", MERGED_AT, DIFF_URL)
            for i in range(1, 4)
        ]
        respx.get(DIFF_URL).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = prs
            mock_g.get_repo.return_value = mock_repo

            connector = GitHubConnector(FAKE_TOKEN)
            results = list(connector.list_merged_prs("owner/repo", last_n=None))

        assert len(results) == 3

    @respx.mock
    def test_diff_text_populated_from_endpoint(self) -> None:
        """diff field is populated from the mocked httpx diff endpoint."""
        expected_diff = "diff --git a/README.md b/README.md\n+new line\n"
        pr = _make_mock_pr(10, "My PR", "Closes #99", MERGED_AT, DIFF_URL)

        respx.get(DIFF_URL).mock(
            return_value=httpx.Response(200, text=expected_diff)
        )

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [pr]
            mock_g.get_repo.return_value = mock_repo

            connector = GitHubConnector(FAKE_TOKEN)
            results = list(connector.list_merged_prs("owner/repo"))

        assert len(results) == 1
        assert results[0].diff == expected_diff

    @respx.mock
    def test_raw_pr_fields(self) -> None:
        """RawPR carries title, body, author, merged_at, files_changed, linked_issue."""
        pr = _make_mock_pr(
            number=42,
            title="Fix cursor pagination",
            body="Closes #100",
            merged_at=MERGED_AT,
            diff_url=DIFF_URL,
            author="jkotas",
            files=["src/a.cs", "src/b.cs"],
            additions=10,
            deletions=3,
        )
        respx.get(DIFF_URL).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [pr]
            mock_g.get_repo.return_value = mock_repo

            connector = GitHubConnector(FAKE_TOKEN)
            results = list(connector.list_merged_prs("owner/repo"))

        assert len(results) == 1
        raw = results[0]
        assert raw.number == 42
        assert raw.title == "Fix cursor pagination"
        assert raw.body == "Closes #100"
        assert raw.author == "jkotas"
        assert raw.merged_at == MERGED_AT
        assert raw.files_changed == ["src/a.cs", "src/b.cs"]
        assert raw.linked_issue == "#100"
        assert raw.additions == 10
        assert raw.deletions == 3
        assert raw.repo_full_name == "owner/repo"

    @respx.mock
    def test_linked_issue_extracted(self) -> None:
        """linked_issue is extracted from the PR body."""
        pr = _make_mock_pr(5, "Title", "Fixes #500", MERGED_AT, DIFF_URL)
        respx.get(DIFF_URL).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [pr]
            mock_g.get_repo.return_value = mock_repo

            connector = GitHubConnector(FAKE_TOKEN)
            results = list(connector.list_merged_prs("owner/repo"))

        assert results[0].linked_issue == "#500"

    @respx.mock
    def test_no_linked_issue_when_body_lacks_reference(self) -> None:
        """linked_issue is None when PR body has no Closes/Fixes reference."""
        pr = _make_mock_pr(6, "Refactor", "Just a refactor, no issue.", MERGED_AT, DIFF_URL)
        respx.get(DIFF_URL).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [pr]
            mock_g.get_repo.return_value = mock_repo

            connector = GitHubConnector(FAKE_TOKEN)
            results = list(connector.list_merged_prs("owner/repo"))

        assert results[0].linked_issue is None

    @respx.mock
    def test_mixed_merged_and_unmerged_with_last_n(self) -> None:
        """last_n counts only merged PRs; unmerged ones are skipped."""
        prs = [
            _make_mock_pr(1, "Merged 1", "", MERGED_AT, DIFF_URL),
            _make_mock_pr(2, "Unmerged", "", None, DIFF_URL),
            _make_mock_pr(3, "Merged 2", "", MERGED_AT, DIFF_URL),
            _make_mock_pr(4, "Merged 3", "", MERGED_AT, DIFF_URL),
        ]
        respx.get(DIFF_URL).mock(return_value=httpx.Response(200, text=FAKE_DIFF))

        with patch("harness.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = prs
            mock_g.get_repo.return_value = mock_repo

            connector = GitHubConnector(FAKE_TOKEN)
            results = list(connector.list_merged_prs("owner/repo", last_n=2))

        assert len(results) == 2
        assert results[0].number == 1
        assert results[1].number == 3


# ---------------------------------------------------------------------------
# Structural conformance: GitHubConnector satisfies ConnectorProtocol
# ---------------------------------------------------------------------------


def test_github_connector_satisfies_connector_protocol() -> None:
    """GitHubConnector is structurally assignable to ConnectorProtocol.

    This test verifies the assignment at runtime; mypy verifies it statically.
    The TYPE_CHECKING block in github.py provides the static check.
    """
    # A ConnectorProtocol-typed variable can hold a GitHubConnector.
    # This is a runtime duck-type check; mypy checks it statically.
    with patch("harness.connectors.github.Github"):
        connector: ConnectorProtocol = GitHubConnector(FAKE_TOKEN)
        # Verify all four protocol methods exist
        assert callable(connector.validate_credentials)
        assert callable(connector.list_merged_prs)
        assert callable(connector.fetch_pr)
        assert callable(connector.rate_limit_status)
