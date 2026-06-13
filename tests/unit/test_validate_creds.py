"""
Unit tests for GitHubConnector.validate_credentials test-read (OPS-01).

Covers:
- Valid token + repo: returns without error (accept)
- 403 response: raises with token-free message
- 404 response: raises with token-free message
- Error messages never contain the token string

Implementation lands in Plan 02 (github.py validate_credentials extension).
Tests are written RED now, turn GREEN when Plan 02 lands.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock
from github import GithubException

import pytest
import respx
import httpx

from senrah.connectors.github import GitHubConnector


FAKE_TOKEN = "ghp_fake_validate_creds_token_9999999"
REPO_FULL_NAME = "owner/testrepo"


class TestValidateCredentialsTestRead:
    """validate_credentials with repo_full_name does a test-read."""

    def test_valid_token_and_repo_does_not_raise(self) -> None:
        """A valid token + accessible repo: validate_credentials succeeds."""
        with patch("senrah.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_user = MagicMock()
            mock_user.login = "valid_user"
            mock_g.get_user.return_value = mock_user

            mock_repo = MagicMock()
            # Simulate a successful first-page fetch (returns iterable with one PR mock)
            mock_pr = MagicMock()
            mock_repo.get_pulls.return_value = [mock_pr]
            mock_g.get_repo.return_value = mock_repo

            conn = GitHubConnector(FAKE_TOKEN)
            # Should not raise
            conn.validate_credentials(repo_full_name=REPO_FULL_NAME)

    def test_403_raises_token_free_message(self) -> None:
        """403 on test-read raises a clear, token-free error."""
        with patch("senrah.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_user = MagicMock()
            mock_user.login = "valid_user"
            mock_g.get_user.return_value = mock_user

            # Simulate 403 on get_repo or get_pulls
            mock_g.get_repo.side_effect = GithubException(403, {"message": "Forbidden"}, {})

            conn = GitHubConnector(FAKE_TOKEN)
            with pytest.raises(Exception) as exc_info:
                conn.validate_credentials(repo_full_name=REPO_FULL_NAME)
            error_msg = str(exc_info.value)
            # Error message must not contain the token
            assert FAKE_TOKEN not in error_msg, (
                f"Token leaked in error message: {error_msg}"
            )

    def test_404_raises_token_free_message(self) -> None:
        """404 on test-read raises a clear, token-free error."""
        with patch("senrah.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_user = MagicMock()
            mock_user.login = "valid_user"
            mock_g.get_user.return_value = mock_user

            mock_g.get_repo.side_effect = GithubException(404, {"message": "Not Found"}, {})

            conn = GitHubConnector(FAKE_TOKEN)
            with pytest.raises(Exception) as exc_info:
                conn.validate_credentials(repo_full_name=REPO_FULL_NAME)
            error_msg = str(exc_info.value)
            assert FAKE_TOKEN not in error_msg, (
                f"Token leaked in error message: {error_msg}"
            )

    def test_auth_only_no_repo_does_not_raise(self) -> None:
        """validate_credentials() without repo_full_name (auth-only) still works."""
        with patch("senrah.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_user = MagicMock()
            mock_user.login = "valid_user"
            mock_g.get_user.return_value = mock_user

            conn = GitHubConnector(FAKE_TOKEN)
            # No repo_full_name — auth-only check
            conn.validate_credentials()

    def test_error_message_is_descriptive(self) -> None:
        """Error message for 403 is descriptive (mentions PR/issues read or similar)."""
        with patch("senrah.connectors.github.Github") as MockGithub:
            mock_g = MockGithub.return_value
            mock_user = MagicMock()
            mock_user.login = "valid_user"
            mock_g.get_user.return_value = mock_user

            mock_g.get_repo.side_effect = GithubException(403, {"message": "Forbidden"}, {})

            conn = GitHubConnector(FAKE_TOKEN)
            with pytest.raises(Exception) as exc_info:
                conn.validate_credentials(repo_full_name=REPO_FULL_NAME)
            error_msg = str(exc_info.value).lower()
            # Message should be descriptive — mentions the issue clearly
            # (exact wording not prescribed, but should not be a raw traceback)
            assert len(error_msg) > 10, "Error message is too short to be descriptive"
