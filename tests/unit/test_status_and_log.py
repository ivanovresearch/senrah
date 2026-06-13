"""
tests/unit/test_status_and_log.py — Phase 5: OPS-04 status plumbing + OPS-05 log.

- search_log: enabled → one line with query text and result count; disabled
  (the default) → NOTHING is written anywhere; newlines in queries escaped.
- McpStatusWriter: flush/record lifecycle, percentile math, read_status on a
  missing file, clean removal.
"""

from __future__ import annotations

import json
import logging

from senrah.mcp.status import McpStatusWriter, read_status
from senrah.search_log import log_search, search_log_enabled


class TestSearchLog:
    def test_disabled_by_default_writes_nothing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("SEARCH_LOG", raising=False)
        log_file = tmp_path / "search.log"
        monkeypatch.setenv("SEARCH_LOG_PATH", str(log_file))
        log_search("secret internal query", 3, source="cli")
        assert not log_file.exists()
        assert not search_log_enabled()

    def test_enabled_appends_query_and_count(self, tmp_path, monkeypatch) -> None:
        log_file = tmp_path / "search.log"
        monkeypatch.setenv("SEARCH_LOG", "true")
        monkeypatch.setenv("SEARCH_LOG_PATH", str(log_file))
        log_search("fix async cancellation", 5, source="mcp")
        log_search("second query", 0, source="cli")
        lines = log_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert "fix async cancellation" in lines[0]
        assert "results=5" in lines[0]
        assert "\tmcp\t" in lines[0]
        assert "results=0" in lines[1]

    def test_newlines_escaped(self, tmp_path, monkeypatch) -> None:
        log_file = tmp_path / "search.log"
        monkeypatch.setenv("SEARCH_LOG", "1")
        monkeypatch.setenv("SEARCH_LOG_PATH", str(log_file))
        log_search("line one\nline two", 1, source="cli")
        lines = log_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1  # the query newline did not split the record

    def test_falsy_values_disable(self, monkeypatch) -> None:
        for v in ("false", "0", "off", ""):
            monkeypatch.setenv("SEARCH_LOG", v)
            assert not search_log_enabled()


class TestMcpStatusWriter:
    def test_flush_writes_payload(self, tmp_path) -> None:
        path = tmp_path / "mcp-status.json"
        w = McpStatusWriter(str(path), transport="stdio")
        w.flush()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["transport"] == "stdio"
        assert data["request_count"] == 0
        assert data["p50_ms"] is None and data["p90_ms"] is None
        assert "pid" in data and "started_at" in data and "updated_at" in data

    def test_record_request_updates_counters_and_percentiles(self, tmp_path) -> None:
        path = tmp_path / "mcp-status.json"
        w = McpStatusWriter(str(path), transport="streamable-http")
        for ms in [10.0, 20.0, 30.0, 40.0, 100.0]:
            w.record_request(ms)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["request_count"] == 5
        assert data["p50_ms"] == 30.0
        assert data["p90_ms"] == 100.0

    def test_remove_and_read_missing(self, tmp_path) -> None:
        path = tmp_path / "mcp-status.json"
        w = McpStatusWriter(str(path), transport="stdio")
        w.flush()
        assert read_status(str(path)) is not None
        w.remove()
        assert read_status(str(path)) is None
        w.remove()  # idempotent
