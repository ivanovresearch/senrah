"""
Unit tests for harness repos command rendering (OPS-02).

Covers:
- repos renders a table with YAML repo list JOIN DB op-state
- A repo with no DB row shows "(never run)" for cursor/last-run
- Output contains repo name, scope, cursor, last_run_at, last_run_status
- No DB writes (listing only, D-A1/D-A2)

Implementation lands in Plan 05 (cli/repos.py).
Tests are written RED now, turn GREEN when Plan 05 lands.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestReposCmd:
    """repos command renders YAML scope JOIN DB op-state."""

    def test_repos_cmd_importable(self) -> None:
        """cli.repos is importable (scaffold test)."""
        try:
            from harness.cli import repos as repos_module  # noqa: F401
        except ImportError:
            pytest.fail("harness.cli.repos is not importable — Plan 05 creates this module")

    def test_repos_shows_never_run_for_missing_op_state(self) -> None:
        """A repo with no DB row shows a 'never run' indicator."""
        try:
            from harness.cli.repos import _format_op_state_row
        except ImportError:
            pytest.skip("repos._format_op_state_row not yet implemented (Plan 05)")

        # A None op_state means no DB row
        row = _format_op_state_row(repo_name="owner/repo", op_state=None)
        assert "never" in row.lower() or "—" in row or "-" in row, (
            f"Expected 'never run' indicator in row, got: {row}"
        )

    def test_repos_shows_cursor_when_op_state_present(self) -> None:
        """A repo with a DB row shows the cursor value."""
        try:
            from harness.cli.repos import _format_op_state_row
            from harness.db.models import RepoOpState
        except ImportError:
            pytest.skip("repos._format_op_state_row not yet implemented (Plan 05)")

        op_state = RepoOpState(
            cursor_merged_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
            cursor_number=42,
            last_run_at=datetime(2024, 5, 10, tzinfo=timezone.utc),
            last_run_status="success",
            last_error=None,
        )
        row = _format_op_state_row(repo_name="owner/repo", op_state=op_state)
        assert "2024" in row or "42" in row, (
            f"Expected cursor info in row, got: {row}"
        )

    def test_get_op_state_not_called_with_write(self) -> None:
        """RepositoryRepo.get_op_state is called (read-only); no write methods called."""
        try:
            from harness.db.repos.repository import RepositoryRepo
        except ImportError:
            pytest.skip("RepositoryRepo.get_op_state not yet implemented (Plan 01/Task 3)")

        mock_conn = MagicMock()
        # get_op_state returns None (no DB row)
        mock_conn.execute.return_value.fetchone.return_value = None

        repo_repo = RepositoryRepo(mock_conn)
        result = repo_repo.get_op_state(project_id=1, name="owner/repo")
        assert result is None

        # Verify no commit was called
        assert not mock_conn.commit.called


_TWO_REPO_YAML = """\
project:
  name: proj
repositories:
  - type: github
    name: owner/repo1
    scope:
      mode: last_n
      value: 50
  - type: github
    name: owner/repo2
ingest:
  default_scope:
    mode: period
    value: 90d
"""


class TestReposCmdRender:
    """Command-level: YAML scope JOIN DB op-state, default fallback, never-run, no writes."""

    def test_repos_table_join_and_never_run(self, tmp_path, monkeypatch, capsys) -> None:
        from harness.cli.repos import repos_cmd
        from harness.db.models import Project, RepoOpState

        (tmp_path / "harness.yaml").write_text(_TWO_REPO_YAML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATABASE_URL", "postgresql://u@localhost/db")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")

        mock_conn = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = mock_conn
        cm.__exit__.return_value = False

        op1 = RepoOpState(
            cursor_merged_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
            cursor_number=42,
            last_run_at=datetime(2024, 5, 10, 9, 0, tzinfo=timezone.utc),
            last_run_status="success",
            last_error=None,
        )

        with patch("harness.cli.repos.connect_sync", return_value=cm), patch(
            "harness.cli.repos.ProjectRepo"
        ) as MockProjectRepo, patch(
            "harness.cli.repos.RepositoryRepo"
        ) as MockRepoRepo:
            MockProjectRepo.return_value.get_by_name.return_value = Project(
                id=1, name="proj"
            )
            repo_repo = MockRepoRepo.return_value
            # repo1 has op-state; repo2 does not (never run)
            repo_repo.get_op_state.side_effect = [op1, None]
            repos_cmd()

        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert any("REPOSITORY" in ln and "STATUS" in ln for ln in lines), "header"

        repo1 = next(ln for ln in lines if "owner/repo1" in ln)
        repo2 = next(ln for ln in lines if "owner/repo2" in ln)
        # repo1: explicit scope + cursor + status
        assert "last_n=50" in repo1
        assert "2024-05-01" in repo1 and "#42" in repo1
        assert "success" in repo1
        # repo2: default_scope fallback + never-run
        assert "period=90d" in repo2
        assert "(never run)" in repo2

        # Read-only: no write methods invoked on the mocked repos
        assert not MockProjectRepo.return_value.upsert.called
        assert not repo_repo.upsert.called
        assert not repo_repo.advance_cursor.called
        assert not repo_repo.set_last_run.called
        assert not mock_conn.commit.called
