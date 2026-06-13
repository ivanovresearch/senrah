"""
Unit tests for `senrah init` (OPS-01).

Covers:
- Accept path: a valid credential → upsert_repo_entry writes the repo; the
  written senrah.yaml passes load_yaml_config + _check_for_secrets.
- Reject path: validate_credentials failure → typer.Exit(1) and NO write.
- The token is never echoed to stdout/stderr.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import typer

from senrah.cli.init import init_cmd
from senrah.config import _check_for_secrets, load_yaml_config

FAKE_TOKEN = "ghp_fake_init_token_DO_NOT_LOG_9999"


def _prompt_sequence(values):
    """Return a typer.prompt replacement that yields the given answers in order,
    honoring the `default` kwarg when a value is the sentinel ...."""
    it = iter(values)

    def _fake_prompt(text, *args, default=None, **kwargs):
        val = next(it)
        return default if val is ... else val

    return _fake_prompt


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@localhost/db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")


class TestInitCmd:
    def test_accept_writes_entry(self, tmp_path, env, monkeypatch, capsys) -> None:
        monkeypatch.chdir(tmp_path)  # find_config_file → None → cwd/senrah.yaml
        cfg_path = tmp_path / "senrah.yaml"
        # Prompts: project, repo type (default github), repo, scope mode, last_n value
        prompts = ["myproj", ..., "owner/repo", "last_n", 25]

        connector = MagicMock()
        connector.validate_credentials.return_value = None  # accept

        with patch(
            "senrah.cli.init.GitHubConnector", return_value=connector
        ), patch("typer.prompt", side_effect=_prompt_sequence(prompts)):
            init_cmd()

        # Validation ran against the entered repo
        connector.validate_credentials.assert_called_once_with(
            repo_full_name="owner/repo"
        )
        # The entry was written and is loadable + secret-free
        cfg = load_yaml_config(cfg_path)
        assert any(r["name"] == "owner/repo" for r in cfg.repositories)
        assert cfg.project_name == "myproj"
        import yaml

        _check_for_secrets(yaml.safe_load(cfg_path.read_text(encoding="utf-8")), cfg_path)

        # The token never appears in stdout/stderr
        out = capsys.readouterr()
        assert FAKE_TOKEN not in out.out and FAKE_TOKEN not in out.err

    def test_reject_does_not_write(self, tmp_path, env, monkeypatch, capsys) -> None:
        monkeypatch.chdir(tmp_path)
        cfg_path = tmp_path / "senrah.yaml"
        prompts = ["myproj", ..., "owner/repo", "all", ...]

        connector = MagicMock()
        connector.validate_credentials.side_effect = RuntimeError(
            "Token cannot read pull requests on 'owner/repo'."
        )

        with patch(
            "senrah.cli.init.GitHubConnector", return_value=connector
        ), patch("typer.prompt", side_effect=_prompt_sequence(prompts)):
            with pytest.raises(typer.Exit) as exc_info:
                init_cmd()

        assert exc_info.value.exit_code == 1
        assert not cfg_path.exists(), "no senrah.yaml must be written on reject"
        out = capsys.readouterr()
        assert FAKE_TOKEN not in out.out and FAKE_TOKEN not in out.err
        assert "REJECTED" in out.err
