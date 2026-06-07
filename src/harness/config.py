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
- upsert_repo_entry uses ruamel.yaml (round-trip) for the INIT write path only.
  ruamel is guarded behind a deferred import so config.py stays importable even
  before Plan 04 installs ruamel.yaml (T-03-SC).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Iterable

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
        # A missing .env is already non-fatal in pydantic-settings (env vars are
        # read directly), so no extra key is needed here.
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
class McpConfig:
    """Non-secret MCP server tunables (D-04 / D-07).

    output_diff_limit: Maximum characters for diff excerpts in search_prs_v1 output (D-04).
    host: Bind address for --transport network (default 127.0.0.1 per D-07).
    port: Bind port for --transport network.
    log_level: FastMCP log level (WARNING by default — minimal output on stdio).
    """

    output_diff_limit: int = 2000
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "WARNING"


@dataclass(frozen=True)
class Scope:
    """Per-repo or project-level ingest scope (D-A3).

    mode: one of "all", "last_n", "since_date", "period"
    value: typed per mode:
      - all: None
      - last_n: int (number of newest merged PRs to backfill)
      - since_date: str (ISO date "YYYY-MM-DD")
      - period: str (duration e.g. "90d")
    """

    mode: str
    value: object = None  # typed per mode; None for "all"


@dataclass
class IngestFilterConfig:
    """Load-filter tunables parsed from the ingest: YAML block (INGEST-03/04).

    stop_list: authors unconditionally excluded (in addition to [bot] suffix)
    max_files: giant-PR threshold (files_changed > max_files → exclude)
    max_lines: giant-PR threshold (additions + deletions > max_lines → exclude)
    rate_limit_floor: pause ingest when remaining < floor (INGEST-06)
    inter_fetch_delay: optional sleep between PR fetches in seconds (0.0 = off)
    """

    stop_list: frozenset[str] = field(default_factory=frozenset)
    max_files: int = 100
    max_lines: int = 5000
    rate_limit_floor: int = 100
    inter_fetch_delay: float = 0.0
    # Incremental-traversal re-yield/break safety window (Design B, Plan 02).
    # The connector applies it; this is the tunable floor. Full run-duration-based
    # derivation (max(floor, last_run_duration × factor)) is deferred — it needs a
    # persisted run-duration column (see 03-FINDINGS-traversal.md).
    overlap_margin_seconds: int = 3600


@dataclass
class YamlConfig:
    project_name: str = ""
    repositories: list[dict] = field(default_factory=list)
    # Legacy field preserved for back-compat — use default_scope instead (D-A3).
    default_last_n: int = 100
    default_scope: Scope = field(default_factory=lambda: Scope(mode="last_n", value=100))
    filters: IngestFilterConfig = field(default_factory=IngestFilterConfig)
    embed: EmbedConfig = field(default_factory=EmbedConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    mcp: McpConfig = field(default_factory=McpConfig)


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


def _parse_scope_block(scope_block: dict | None) -> Scope | None:
    """Parse a {mode, value} dict into a Scope, or return None."""
    if not scope_block or not isinstance(scope_block, dict):
        return None
    mode = scope_block.get("mode")
    if not mode:
        return None
    value = scope_block.get("value")
    return Scope(mode=mode, value=value)


def _parse_default_scope(ingest_block: dict) -> Scope:
    """Derive the default Scope from the ingest block with back-compat.

    Resolution order (D-A3):
    1. ingest.default_scope: {mode, value}  ← preferred
    2. ingest.default_last_n: N             ← legacy, synthesized as last_n/N
    3. Default: last_n/100
    """
    default_scope_block = ingest_block.get("default_scope")
    if default_scope_block:
        parsed = _parse_scope_block(default_scope_block)
        if parsed:
            return parsed

    # Back-compat: legacy default_last_n → synthesize Scope(last_n, N)
    legacy_n = ingest_block.get("default_last_n")
    if legacy_n is not None:
        return Scope(mode="last_n", value=int(legacy_n))

    return Scope(mode="last_n", value=100)


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

    # Parse repositories (keep raw dicts — consumers read scope etc. from them)
    repositories = raw.get("repositories", []) or []

    # Parse ingest block
    ingest_block = raw.get("ingest", {}) or {}
    default_last_n = ingest_block.get("default_last_n", 100)
    default_scope = _parse_default_scope(ingest_block)

    # Parse filter knobs from ingest block (INGEST-03)
    raw_stop_list = ingest_block.get("stop_list", []) or []
    filters = IngestFilterConfig(
        stop_list=frozenset(raw_stop_list),
        max_files=ingest_block.get("max_files", 100),
        max_lines=ingest_block.get("max_lines", 5000),
        rate_limit_floor=ingest_block.get("rate_limit_floor", 100),
        inter_fetch_delay=float(ingest_block.get("inter_fetch_delay", 0.0)),
        overlap_margin_seconds=int(ingest_block.get("overlap_margin_seconds", 3600)),
    )

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

    # Parse mcp block (D-04 / D-07)
    mcp_block = raw.get("mcp", {}) or {}
    mcp_cfg = McpConfig(
        output_diff_limit=mcp_block.get("output_diff_limit", 2000),
        host=mcp_block.get("host", "127.0.0.1"),
        port=mcp_block.get("port", 8000),
        log_level=mcp_block.get("log_level", "WARNING"),
    )

    return YamlConfig(
        project_name=project_name,
        repositories=repositories,
        default_last_n=default_last_n,
        default_scope=default_scope,
        filters=filters,
        embed=embed_cfg,
        search=search_cfg,
        mcp=mcp_cfg,
    )


# ---------------------------------------------------------------------------
# Scope resolver — pure function, I/O-free (unit-testable) — INGEST-04
# ---------------------------------------------------------------------------


def resolve_since(
    scope: Scope,
    *,
    now: datetime,
    last_n_merged_at_provider: Iterable[datetime] | None = None,
) -> datetime | None:
    """Translate a Scope to a UTC datetime lower bound (or None for all history).

    Pattern 3 table:
    - mode=all        → None (no lower bound; fetch repo history)
    - mode=since_date → parse value as ISO date, return midnight UTC
    - mode=period     → now - timedelta(days=N) where value is "Nd"
    - mode=last_n     → oldest of newest N merged_at from last_n_merged_at_provider
                        (returns None if no provider given — caller must supply data)

    The resolver is I/O-free so unit tests need no network. The Ingester supplies
    the last_n_merged_at_provider as needed (a pre-fetched list of merged_at values).
    """
    if scope.mode == "all":
        return None

    if scope.mode == "since_date":
        # Parse ISO date string to UTC datetime
        d = date.fromisoformat(str(scope.value))
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

    if scope.mode == "period":
        # Parse "Nd" duration string (e.g. "90d")
        duration_str = str(scope.value)
        if duration_str.endswith("d"):
            days = int(duration_str[:-1])
            return now - timedelta(days=days)
        raise ValueError(f"Unsupported period format: {duration_str!r} (expected 'Nd')")

    if scope.mode == "last_n":
        if last_n_merged_at_provider is None:
            return None
        n = int(scope.value)  # type: ignore[arg-type]
        # Collect all dates and return the oldest of the newest N
        all_dates = sorted(last_n_merged_at_provider)  # ascending
        if not all_dates:
            return None
        if len(all_dates) <= n:
            return all_dates[0]  # all dates are "newest N" — return oldest overall
        # Return the oldest of the newest N (i.e. element at index -(n))
        return all_dates[-n]

    raise ValueError(f"Unknown scope mode: {scope.mode!r}")


# ---------------------------------------------------------------------------
# ruamel YAML writer for `harness init` (D-A2/D-A3)
# T-03-SC: ruamel is not yet installed (gated behind Plan 04 checkpoint).
# Guard the import inside the function so config.py stays importable until then.
# ---------------------------------------------------------------------------


def upsert_repo_entry(
    path: Path,
    *,
    repo_name: str,
    repo_type: str,
    scope: Scope | None,
    project_name: str | None = None,
) -> None:
    """Merge a repository entry into harness.yaml, preserving comments + blocks.

    Uses ruamel.yaml round-trip mode (YAML(typ="rt"), preserve_quotes=True) so
    that existing comments, key order, and sibling blocks (embed/search/mcp)
    are not destroyed (D-A2). Re-runs _check_for_secrets on the serialized
    output before writing (T-03-01).

    If ruamel.yaml is not installed, raises ImportError with a clear message.
    Run Plan 04 to install it (gated behind the blocking-human checkpoint for
    package legitimacy verification).
    """
    try:
        from ruamel.yaml import YAML as RuamelYAML
    except ImportError as exc:
        raise ImportError(
            "ruamel.yaml is required for harness init's YAML writer but is not installed. "
            "Run Plan 04 to install it (gated behind a package-legitimacy checkpoint)."
        ) from exc

    yaml_rt = RuamelYAML(typ="rt")
    yaml_rt.preserve_quotes = True

    # Load existing file, or start with an empty mapping
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            data = yaml_rt.load(fh) or {}
    else:
        data = {}

    # Set project name on first run (only if not already set)
    if project_name:
        project_block = data.setdefault("project", {})
        if not project_block.get("name"):
            project_block["name"] = project_name

    # Find or create the repository entry
    repos = data.setdefault("repositories", [])
    existing = next((r for r in repos if r.get("name") == repo_name), None)
    entry = existing if existing is not None else {"type": repo_type, "name": repo_name}

    # Set/update the scope (D-A3 structured shape)
    if scope is not None:
        entry["scope"] = {"mode": scope.mode, "value": scope.value}

    if existing is None:
        repos.append(entry)

    # Migrate legacy ingest.default_last_n → ingest.default_scope when writing
    ingest_block = data.get("ingest", {})
    if ingest_block and "default_last_n" in ingest_block and "default_scope" not in ingest_block:
        n = ingest_block.pop("default_last_n")
        ingest_block["default_scope"] = {"mode": "last_n", "value": n}

    # T-03-01: re-run _check_for_secrets on the serialized output BEFORE writing
    buf = StringIO()
    yaml_rt.dump(data, buf)
    _check_for_secrets(yaml.safe_load(buf.getvalue()), path)

    # Write to disk (comment-preserving)
    with open(path, "w", encoding="utf-8") as fh:
        yaml_rt.dump(data, fh)
