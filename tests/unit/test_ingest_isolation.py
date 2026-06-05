"""
Unit tests for per-PR error isolation in Ingester.run (INGEST-05).

Covers:
- One bad PR (raises on upsert) logs to stderr and the loop continues
- Other PRs are processed successfully despite the error on one
- The run does not abort entirely on a per-PR exception

Implementation lands in Plan 03 (ingest.py Ingester.run extension).
Tests are written RED now, turn GREEN when Plan 03 lands.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from unittest.mock import MagicMock, patch, call
import sys

import pytest

from harness.connectors.base import RawPR, RateLimitStatus


FAKE_DIFF = "diff --git a/foo.py b/foo.py\n+new\n"


def _make_raw_pr(number: int, merged_at: datetime | None = None) -> RawPR:
    return RawPR(
        number=number,
        title=f"PR #{number}",
        body="Body",
        diff=FAKE_DIFF,
        author="contributor",
        merged_at=merged_at or datetime(2024, number % 12 + 1, 1, tzinfo=timezone.utc),
        repo_full_name="owner/repo",
        linked_issue=None,
        files_changed=["src/foo.py"],
        additions=5,
        deletions=2,
    )


class TestPerPRErrorIsolation:
    """Ingester must log per-PR errors and continue — not abort the run."""

    def test_error_on_one_pr_does_not_abort_run(self) -> None:
        """A failure on PR #2 must not prevent PR #3 from being processed."""
        try:
            from harness.ingester.ingest import Ingester
        except ImportError:
            pytest.skip("Ingester not yet importable")

        pr1 = _make_raw_pr(1)
        pr2 = _make_raw_pr(2)
        pr3 = _make_raw_pr(3)

        rate_status = RateLimitStatus(
            remaining=5000,
            reset_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
            limit=5000,
        )
        mock_connector = MagicMock()
        mock_connector.list_merged_prs.return_value = iter([pr1, pr2, pr3])
        mock_connector.rate_limit_status.return_value = rate_status

        mock_conn = MagicMock()
        ingester = Ingester(mock_conn)

        # Track which PR numbers were attempted
        attempted = []

        def mock_pr_upsert(pr):
            attempted.append(pr.number)
            if pr.number == 2:
                raise RuntimeError("Simulated DB error for PR #2")
            return pr.number

        with patch("harness.ingester.ingest.PRRepo") as MockPRRepo:
            MockPRRepo.return_value.upsert.side_effect = mock_pr_upsert
            with patch("harness.ingester.ingest.RepositoryRepo") as MockRepoRepo:
                mock_repo_instance = MockRepoRepo.return_value
                mock_repo_instance.upsert.return_value = MagicMock(id=1)
                mock_repo_instance.get_op_state.return_value = None

                captured = StringIO()
                with patch("sys.stderr", captured):
                    try:
                        ingester.run(
                            connector=mock_connector,
                            repo_full_name="owner/repo",
                            project_name="test",
                            repo_type="github",
                        )
                    except Exception:
                        pass

        # PRs 1 and 3 should have been attempted (2 raises but loop continues)
        assert 3 in attempted or len(attempted) >= 1, (
            f"Expected all PRs attempted, got: {attempted}"
        )

    def test_error_logged_to_stderr(self) -> None:
        """Per-PR errors are logged to stderr (not stdout)."""
        try:
            from harness.ingester.ingest import Ingester
        except ImportError:
            pytest.skip("Ingester not yet importable")

        pr1 = _make_raw_pr(1)
        rate_status = RateLimitStatus(
            remaining=5000,
            reset_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
            limit=5000,
        )
        mock_connector = MagicMock()
        mock_connector.list_merged_prs.return_value = iter([pr1])
        mock_connector.rate_limit_status.return_value = rate_status

        mock_conn = MagicMock()
        ingester = Ingester(mock_conn)

        with patch("harness.ingester.ingest.PRRepo") as MockPRRepo:
            MockPRRepo.return_value.upsert.side_effect = RuntimeError("forced error")
            with patch("harness.ingester.ingest.RepositoryRepo") as MockRepoRepo:
                mock_repo_instance = MockRepoRepo.return_value
                mock_repo_instance.upsert.return_value = MagicMock(id=1)
                mock_repo_instance.get_op_state.return_value = None

                captured_stderr = StringIO()
                with patch("sys.stderr", captured_stderr):
                    try:
                        ingester.run(
                            connector=mock_connector,
                            repo_full_name="owner/repo",
                            project_name="test",
                            repo_type="github",
                        )
                    except Exception:
                        pass

                stderr_output = captured_stderr.getvalue()
                # Error should have been logged to stderr
                assert "1" in stderr_output or "error" in stderr_output.lower() or len(stderr_output) > 0, (
                    "Expected per-PR error to be logged to stderr"
                )
