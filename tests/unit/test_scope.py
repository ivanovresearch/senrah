"""
Unit tests for senrah.config scope model and resolver (INGEST-04).

Covers:
- Scope dataclass creation
- scope→since resolution per mode: all, since_date, period, last_n
- test_last_n_window: last_n resolves to oldest-of-newest-N merged_at
- load_yaml_config reads ingest.default_scope: {mode, value}
- Legacy ingest.default_last_n back-compat: synthesized Scope(last_n, N)
- Per-repo scope: {mode, value} parses onto repository entries
- Filter knobs (stop_list, max_files, max_lines, rate_limit_floor, inter_fetch_delay)

No I/O beyond tmp file writes. No network.
"""

from __future__ import annotations

from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import pytest

from senrah.config import Scope, resolve_since, load_yaml_config, YamlConfig


# ---------------------------------------------------------------------------
# Scope dataclass tests
# ---------------------------------------------------------------------------

class TestScopeDataclass:
    def test_last_n_scope(self) -> None:
        s = Scope(mode="last_n", value=200)
        assert s.mode == "last_n"
        assert s.value == 200

    def test_all_scope(self) -> None:
        s = Scope(mode="all", value=None)
        assert s.mode == "all"
        assert s.value is None

    def test_since_date_scope(self) -> None:
        s = Scope(mode="since_date", value="2024-01-01")
        assert s.mode == "since_date"

    def test_period_scope(self) -> None:
        s = Scope(mode="period", value="90d")
        assert s.mode == "period"

    def test_frozen(self) -> None:
        """Scope is frozen (immutable)."""
        s = Scope(mode="last_n", value=10)
        with pytest.raises((AttributeError, TypeError)):
            s.value = 20  # type: ignore[misc]


# ---------------------------------------------------------------------------
# resolve_since tests — I/O-free resolver
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


class TestResolveSince:
    def test_mode_all_returns_none(self) -> None:
        """mode=all → no lower bound."""
        scope = Scope(mode="all", value=None)
        result = resolve_since(scope, now=FIXED_NOW)
        assert result is None

    def test_mode_since_date_returns_utc_datetime(self) -> None:
        """mode=since_date with ISO date string → UTC datetime."""
        scope = Scope(mode="since_date", value="2024-01-01")
        result = resolve_since(scope, now=FIXED_NOW)
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1
        assert result.tzinfo is not None

    def test_mode_period_90d(self) -> None:
        """mode=period '90d' → now - 90 days."""
        scope = Scope(mode="period", value="90d")
        result = resolve_since(scope, now=FIXED_NOW)
        expected = FIXED_NOW - timedelta(days=90)
        assert result is not None
        assert result == expected

    def test_mode_period_30d(self) -> None:
        scope = Scope(mode="period", value="30d")
        result = resolve_since(scope, now=FIXED_NOW)
        expected = FIXED_NOW - timedelta(days=30)
        assert result == expected

    def test_mode_last_n_no_provider_returns_none(self) -> None:
        """mode=last_n with no provider → None (no data to compute window)."""
        scope = Scope(mode="last_n", value=10)
        result = resolve_since(scope, now=FIXED_NOW)
        assert result is None

    def test_mode_last_n_with_provider_returns_oldest_of_newest_n(self) -> None:
        """mode=last_n=2 with 5 candidates → 3rd-oldest (oldest of newest 2)."""
        merged_dates = [
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 2, 1, tzinfo=timezone.utc),
            datetime(2024, 3, 1, tzinfo=timezone.utc),
            datetime(2024, 4, 1, tzinfo=timezone.utc),
            datetime(2024, 5, 1, tzinfo=timezone.utc),
        ]
        scope = Scope(mode="last_n", value=2)
        result = resolve_since(scope, now=FIXED_NOW, last_n_merged_at_provider=merged_dates)
        # newest 2 are April and May → oldest of those = April 1
        assert result == datetime(2024, 4, 1, tzinfo=timezone.utc)

    def test_last_n_window(self) -> None:
        """INGEST-04: last_n resolves to oldest-of-newest-N window boundary."""
        merged_dates = [
            datetime(2024, 1, 10, tzinfo=timezone.utc),
            datetime(2024, 2, 20, tzinfo=timezone.utc),
            datetime(2024, 3, 15, tzinfo=timezone.utc),
            datetime(2024, 4, 5, tzinfo=timezone.utc),
            datetime(2024, 5, 25, tzinfo=timezone.utc),
        ]
        scope = Scope(mode="last_n", value=3)
        result = resolve_since(scope, now=FIXED_NOW, last_n_merged_at_provider=merged_dates)
        # newest 3: March 15, April 5, May 25 → oldest = March 15
        assert result == datetime(2024, 3, 15, tzinfo=timezone.utc)

    def test_last_n_provider_fewer_than_n(self) -> None:
        """When provider has fewer items than n, return the oldest overall."""
        merged_dates = [
            datetime(2024, 2, 1, tzinfo=timezone.utc),
            datetime(2024, 3, 1, tzinfo=timezone.utc),
        ]
        scope = Scope(mode="last_n", value=10)
        result = resolve_since(scope, now=FIXED_NOW, last_n_merged_at_provider=merged_dates)
        # fewer than n → return the earliest available
        assert result == datetime(2024, 2, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# load_yaml_config: default_scope + back-compat + per-repo scope + filter knobs
# ---------------------------------------------------------------------------

class TestLoadYamlConfigScope:
    def test_ingest_default_scope_parsed(self, tmp_path) -> None:
        """ingest.default_scope: {mode, value} is parsed into Scope."""
        yaml_content = """
project:
  name: myproject
ingest:
  default_scope:
    mode: last_n
    value: 50
repositories: []
"""
        cfg_path = tmp_path / "senrah.yaml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        cfg = load_yaml_config(cfg_path)
        assert cfg.default_scope.mode == "last_n"
        assert cfg.default_scope.value == 50

    def test_legacy_default_last_n_back_compat(self, tmp_path) -> None:
        """Legacy ingest.default_last_n: 100 synthesizes Scope(last_n, 100)."""
        yaml_content = """
project:
  name: myproject
ingest:
  default_last_n: 100
repositories: []
"""
        cfg_path = tmp_path / "senrah.yaml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        cfg = load_yaml_config(cfg_path)
        assert cfg.default_scope.mode == "last_n"
        assert cfg.default_scope.value == 100

    def test_neither_scope_nor_last_n_uses_default(self, tmp_path) -> None:
        """When neither default_scope nor default_last_n is present, default to last_n/100."""
        yaml_content = """
project:
  name: myproject
repositories: []
"""
        cfg_path = tmp_path / "senrah.yaml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        cfg = load_yaml_config(cfg_path)
        assert cfg.default_scope.mode == "last_n"
        assert cfg.default_scope.value == 100

    def test_per_repo_scope_parsed(self, tmp_path) -> None:
        """Per-repo scope: {mode, value} is parsed onto repository entries."""
        yaml_content = """
project:
  name: myproject
ingest:
  default_scope: {mode: last_n, value: 50}
repositories:
  - type: github
    name: owner/repo1
    scope: {mode: since_date, value: "2024-01-01"}
  - type: github
    name: owner/repo2
"""
        cfg_path = tmp_path / "senrah.yaml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        cfg = load_yaml_config(cfg_path)
        # Check that repository entries contain scope
        repos = cfg.repositories
        assert len(repos) == 2
        # repo1 has explicit scope
        repo1 = repos[0]
        assert "scope" in repo1
        # repo2 has no scope → uses default
        repo2 = repos[1]
        assert "scope" not in repo2 or repo2.get("scope") is None

    def test_filter_knobs_parsed(self, tmp_path) -> None:
        """Filter knobs from ingest block are parsed with correct defaults."""
        yaml_content = """
project:
  name: myproject
ingest:
  default_scope: {mode: all, value: null}
  stop_list: [dependabot, renovate]
  max_files: 200
  max_lines: 10000
  rate_limit_floor: 50
  inter_fetch_delay: 0.5
repositories: []
"""
        cfg_path = tmp_path / "senrah.yaml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        cfg = load_yaml_config(cfg_path)
        assert "dependabot" in cfg.filters.stop_list
        assert "renovate" in cfg.filters.stop_list
        assert cfg.filters.max_files == 200
        assert cfg.filters.max_lines == 10000
        assert cfg.filters.rate_limit_floor == 50
        assert cfg.filters.inter_fetch_delay == 0.5

    def test_filter_knobs_defaults(self, tmp_path) -> None:
        """Filter knobs default when absent from YAML."""
        yaml_content = """
project:
  name: myproject
repositories: []
"""
        cfg_path = tmp_path / "senrah.yaml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        cfg = load_yaml_config(cfg_path)
        assert cfg.filters.max_files == 100
        assert cfg.filters.max_lines == 5000
        assert cfg.filters.rate_limit_floor == 100
        assert cfg.filters.inter_fetch_delay == 0.0

    def test_existing_senrah_yaml_example_back_compat(self) -> None:
        """The repo's own senrah.yaml.example (legacy default_last_n) still parses."""
        repo_root = Path(__file__).parent.parent.parent
        yaml_path = repo_root / "senrah.yaml.example"
        if not yaml_path.exists():
            pytest.skip("senrah.yaml.example not found")
        # Must not raise
        cfg = load_yaml_config(yaml_path)
        # Legacy default_last_n → synthesized Scope
        assert cfg.default_scope.mode == "last_n"
        assert cfg.default_scope.value == 100
