"""
tests/unit/test_secrets_hygiene.py

Verifies OPS-06 / T-01-01 / T-01-02 hygiene:
- .env appears in .gitignore (so real secrets can never be accidentally committed)
- .env.example exists with the three required placeholder keys
- .env.example contains NO token-shaped values (no real secrets)
- load_yaml_config raises when a secret key appears in the YAML
- harness.yaml.example matches the D-03 locked config shape
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repo root is two levels up from tests/unit/
REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# T-01-01: .env is git-ignored
# ---------------------------------------------------------------------------


def test_dotenv_is_gitignored():
    """Assert that .env appears in .gitignore."""
    gitignore_path = REPO_ROOT / ".gitignore"
    assert gitignore_path.exists(), ".gitignore does not exist"

    content = gitignore_path.read_text(encoding="utf-8")
    # Match standalone .env line (not .env.example)
    lines = [line.strip() for line in content.splitlines()]
    assert ".env" in lines, (
        ".env must appear as a standalone line in .gitignore. "
        "This prevents accidental credential commits (OPS-06 / T-01-01)."
    )


# ---------------------------------------------------------------------------
# T-01-01 / OPS-06: .env.example exists with placeholder keys
# ---------------------------------------------------------------------------


def test_dotenv_example_exists():
    """.env.example must exist in the repo root."""
    assert (REPO_ROOT / ".env.example").exists(), ".env.example does not exist"


def test_dotenv_example_has_required_keys():
    """Assert that .env.example contains the three required secret placeholders."""
    content = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    required_keys = ["DATABASE_URL", "GITHUB_TOKEN", "OPENAI_API_KEY"]
    for key in required_keys:
        assert key in content, (
            f".env.example is missing required key: {key}. "
            "All three secret keys must be documented as placeholders."
        )


def test_dotenv_example_has_no_real_secrets():
    """.env.example must not contain token-shaped values.

    Guards against accidentally committing an .env.example that contains
    real credentials. Checks for known token prefixes and patterns.
    """
    content = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    # Patterns that would indicate a real credential
    real_secret_patterns = [
        # GitHub tokens
        r"ghp_[A-Za-z0-9_]{36,}",       # classic PAT
        r"github_pat_[A-Za-z0-9_]{82,}", # fine-grained PAT
        r"gho_[A-Za-z0-9_]{36,}",        # OAuth token
        # OpenAI API key
        r"sk-[A-Za-z0-9]{20,}",
        # PostgreSQL DSN with real password (has non-placeholder password)
        r"postgresql://\w+:[^p][^a][^s][^s][^w][^o][^r][^d@]{4,}@",
    ]

    for pattern in real_secret_patterns:
        match = re.search(pattern, content)
        assert match is None, (
            f".env.example appears to contain a real secret "
            f"(matched pattern: {pattern!r}, value: {match.group()!r}). "
            "Replace it with a placeholder."
        )


# ---------------------------------------------------------------------------
# T-01-02: load_yaml_config rejects secret keys
# ---------------------------------------------------------------------------


def test_load_yaml_config_rejects_secrets(tmp_path: Path):
    """load_yaml_config must raise ValueError if a secret key is in the YAML."""
    from harness.config import load_yaml_config

    bad_yaml = tmp_path / "harness.yaml"
    bad_yaml.write_text(
        "project:\n  name: test\ngithub_token: ghp_fake\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="github_token"):
        load_yaml_config(bad_yaml)


def test_load_yaml_config_rejects_database_url(tmp_path: Path):
    """load_yaml_config must raise ValueError if DATABASE_URL is in the YAML."""
    from harness.config import load_yaml_config

    bad_yaml = tmp_path / "harness.yaml"
    bad_yaml.write_text(
        "project:\n  name: test\ndatabase_url: postgresql://u:p@localhost/db\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="database_url"):
        load_yaml_config(bad_yaml)


def test_load_yaml_config_rejects_openai_api_key(tmp_path: Path):
    """load_yaml_config must raise ValueError if openai_api_key is in the YAML."""
    from harness.config import load_yaml_config

    bad_yaml = tmp_path / "harness.yaml"
    bad_yaml.write_text(
        "project:\n  name: test\nopenai_api_key: sk-fake\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="openai_api_key"):
        load_yaml_config(bad_yaml)


# ---------------------------------------------------------------------------
# D-03: harness.yaml.example matches the locked config shape
# ---------------------------------------------------------------------------


def test_harness_yaml_example_exists():
    """harness.yaml.example must exist in the repo root."""
    assert (REPO_ROOT / "harness.yaml.example").exists(), "harness.yaml.example does not exist"


def test_harness_yaml_example_d03_shape():
    """harness.yaml.example must contain all D-03 locked keys and default values."""
    from harness.config import load_yaml_config

    path = REPO_ROOT / "harness.yaml.example"
    cfg = load_yaml_config(path)

    assert cfg.project_name == "sample", (
        f"D-03: project.name must be 'sample', got {cfg.project_name!r}"
    )
    assert len(cfg.repositories) == 1, "D-03: repositories must have one entry"
    assert cfg.repositories[0]["type"] == "github"
    assert cfg.repositories[0]["name"] == "dotnet/runtime"

    assert cfg.default_last_n == 100, (
        f"D-03: ingest.default_last_n must be 100, got {cfg.default_last_n}"
    )

    # Embed defaults
    assert cfg.embed.model == "text-embedding-3-small"
    assert cfg.embed.version == "v1"
    assert cfg.embed.problem_limit_tokens == 1500
    assert cfg.embed.diff_limit_tokens == 6000

    # Search defaults
    assert cfg.search.top_n == 5
    assert abs(cfg.search.score_threshold - 0.40) < 1e-9
    assert abs(cfg.search.problem_weight - 0.6) < 1e-9
    assert abs(cfg.search.solution_weight - 0.4) < 1e-9
    assert cfg.search.oversample_factor == 5


def test_load_yaml_config_valid_returns_yaml_config():
    """load_yaml_config on the example file returns a YamlConfig instance."""
    from harness.config import YamlConfig, load_yaml_config

    path = REPO_ROOT / "harness.yaml.example"
    cfg = load_yaml_config(path)
    assert isinstance(cfg, YamlConfig)
