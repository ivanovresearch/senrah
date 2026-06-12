"""
harness.mcp.status — MCP server status file for `harness status` (OPS-04).

`harness serve` (real mode only) maintains a small JSON heartbeat file:

    {"pid": …, "transport": "stdio"|"streamable-http", "started_at": ISO,
     "updated_at": ISO, "request_count": N, "p50_ms": …, "p90_ms": …}

- updated on every search_prs_v1 call (latency recorded into a bounded
  reservoir) and by a 30s heartbeat task, so `harness status` can tell a
  live server (fresh updated_at) from a crashed one (stale file);
- deleted on clean shutdown — a missing file means "down".

The file contains NO query text and no secrets — counters and timestamps
only. Path comes from mcp.status_file in harness.yaml.
"""

from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime, timezone

# Heartbeat cadence; `harness status` treats updated_at older than
# 3 * HEARTBEAT_SECONDS as a stale (likely crashed) server.
HEARTBEAT_SECONDS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class McpStatusWriter:
    """Owns the status file lifecycle for one server process."""

    def __init__(self, path: str, transport: str) -> None:
        self._path = path
        self._transport = transport
        self._started_at = _now()
        self._request_count = 0
        self._latencies_ms: deque[float] = deque(maxlen=512)

    def record_request(self, latency_ms: float) -> None:
        self._request_count += 1
        self._latencies_ms.append(latency_ms)
        self.flush()

    def _percentile(self, p: float) -> float | None:
        if not self._latencies_ms:
            return None
        ordered = sorted(self._latencies_ms)
        idx = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
        return round(ordered[idx], 1)

    def flush(self) -> None:
        payload = {
            "pid": os.getpid(),
            "transport": self._transport,
            "started_at": self._started_at,
            "updated_at": _now(),
            "request_count": self._request_count,
            "p50_ms": self._percentile(0.50),
            "p90_ms": self._percentile(0.90),
        }
        try:
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except OSError:
            pass  # status reporting must never break the server

    def remove(self) -> None:
        try:
            os.remove(self._path)
        except OSError:
            pass


def read_status(path: str) -> dict | None:
    """Read the status file; None when missing/unreadable (server is down)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
