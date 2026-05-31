"""
harness.config — ENV secrets + YAML config loader.

Secrets (DATABASE_URL, GITHUB_TOKEN, OPENAI_API_KEY) come only from ENV.
Non-secret tunables come from harness.yaml (discovered by walking up from cwd).

Design:
- EnvSettings uses pydantic-settings BaseSettings → reads from ENV / .env file.
- YamlConfig is a plain dataclass → populated by load_yaml_config().
- load_yaml_config() uses yaml.safe_load (never yaml.load — ASVS V5 / T-01-03).
- load_yaml_config() raises ValueError if any secret key is present in the YAML
  (prevents accidentally putting secrets in the config file — T-01-02).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Secret keys that are forbidden in harness.yaml (T-01-02)
# ---------------------------------------------------------------------------
_SECRET_YAML_KEYS = frozenset(
    {
        "database_url",
        "github_token",
        "openai_api_key",
        # common case-variations and aliases
        "db_url",
        "db_dsn",
        "github_pat",
        "openai_key",
    }
)


# ---------------------------------------------------------------------------
# ENV secrets (BaseSettings — read from environment / .env file)
# ---------------------------------------------------------------------------


class EnvSettings(BaseSettings):
    """ENV-only secrets. Never sourced from YAML."""

    database_url: str
    github_token: str
    openai_api_key: str

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # Don't crash if .env is absent — CI typically sets vars directly
        "env_file_override": False,
    }


# ---------------------------------------------------------------------------
# YAML config dataclasses (non-secret tunables — D-01, D-03)
# ---------------------------------------------------------------------------


@dataclass
class EmbedConfig:
    model: str = "text-embedding-3-small"
    version: str = "v1"
    problem_limit_tokens: int = 1500
    diff_limit_tokens: int = 6000
    # Optional OpenAI-compatible endpoint (non-secret — URL only). Set to
    # https://openrouter.ai/api/v1 to route embeddings through OpenRouter, or
    # an Azure / local endpoint. None → OpenAI default (api.openai.com).
    base_url: str | None = None


@dataclass
class SearchConfig:
    top_n: int = 5
    score_threshold: float = 0.40
    problem_weight: float = 0.6
    solution_weight: float = 0.4
    oversample_factor: int = 5


@dataclass
class YamlConfig:
    project_name: str = ""
    repositories: list[dict] = field(default_factory=list)
    default_last_n: int = 100
    embed: EmbedConfig = field(default_factory=EmbedConfig)
    search: SearchConfig = field(default_factory=SearchConfig)


# ---------------------------------------------------------------------------
# Config file discovery (cwd walk-up, stop at .git or fs root — D-03)
# ---------------------------------------------------------------------------


def find_config_file(name: str = "harness.yaml") -> Path | None:
    """Walk from cwd upward, looking for `name`.

    Stops when a .git directory is found (repo root boundary) or when the
    filesystem root is reached.  Returns None if not found.

    Mirrors the discovery strategy used by ruff, black, and git itself.
    """
    current = Path.cwd()
    for directory in [current, *current.parents]:
        candidate = directory / name
        if candidate.exists():
            return candidate
        # Stop at repo root — don't walk outside the project
        if (directory / ".git").exists():
            break
    return None


# ---------------------------------------------------------------------------
# YAML config loader
# ---------------------------------------------------------------------------


def _check_for_secrets(raw: dict, path: Path) -> None:
    """Raise ValueError if any secret key is present anywhere in the YAML.

    Performs a case-insensitive check on all top-level and known nested keys.
    """
    if not isinstance(raw, dict):
        return

    def _flatten(d: dict, prefix: str = "") -> list[str]:
        keys = []
        for k, v in d.items():
            full_key = f"{prefix}.{k}" if prefix else str(k)
            keys.append(full_key.lower().replace(".", "_"))
            if isinstance(v, dict):
                keys.extend(_flatten(v, full_key))
        return keys

    all_keys = _flatten(raw)
    for secret_key in _SECRET_YAML_KEYS:
        for yaml_key in all_keys:
            if secret_key == yaml_key or yaml_key.endswith(f"_{secret_key}") or yaml_key.endswith(f".{secret_key}"):
                raise ValueError(
                    f"Secret key '{secret_key}' found in {path}. "
                    "Secrets must come only from environment variables. "
                    "Remove it from the YAML file and set it as an ENV var instead."
                )


def load_yaml_config(path: Path) -> YamlConfig:
    """Parse harness.yaml into a YamlConfig dataclass.

    Uses yaml.safe_load (never yaml.load) — ASVS V5 input validation / T-01-03.
    Raises ValueError if any secret key is detected in the YAML — T-01-02.
    """
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    # Security check: reject secrets in YAML (T-01-02)
    _check_for_secrets(raw, path)

    # Parse project block
    project_block = raw.get("project", {}) or {}
    project_name = project_block.get("name", "")

    # Parse repositories
    repositories = raw.get("repositories", []) or []

    # Parse ingest block
    ingest_block = raw.get("ingest", {}) or {}
    default_last_n = ingest_block.get("default_last_n", 100)

    # Parse embed block
    embed_block = raw.get("embed", {}) or {}
    embed_cfg = EmbedConfig(
        model=embed_block.get("model", "text-embedding-3-small"),
        version=embed_block.get("version", "v1"),
        problem_limit_tokens=embed_block.get("problem_limit_tokens", 1500),
        diff_limit_tokens=embed_block.get("diff_limit_tokens", 6000),
        base_url=embed_block.get("base_url") or None,
    )

    # Parse search block
    search_block = raw.get("search", {}) or {}
    search_cfg = SearchConfig(
        top_n=search_block.get("top_n", 5),
        score_threshold=search_block.get("score_threshold", 0.40),
        problem_weight=search_block.get("problem_weight", 0.6),
        solution_weight=search_block.get("solution_weight", 0.4),
        oversample_factor=search_block.get("oversample_factor", 5),
    )

    return YamlConfig(
        project_name=project_name,
        repositories=repositories,
        default_last_n=default_last_n,
        embed=embed_cfg,
        search=search_cfg,
    )
