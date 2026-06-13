"""
senrah.search_log — opt-in search query logging (OPS-05).

PRIVACY NOTE: when enabled, the FULL QUERY TEXT is appended to a local log
file. On private repositories query text can reveal proprietary code or
internal problem descriptions — leave this OFF (the default) unless you
need retrieval debugging, and treat the log file itself as sensitive.

Config (environment variables, read at call time):
- SEARCH_LOG:       "true" / "1" enables logging. Anything else (or unset)
                    disables it — the default writes NOTHING anywhere.
- SEARCH_LOG_PATH:  log file path; default "senrah-search.log" in cwd.

Both the CLI (`senrah search`) and the MCP tool (`search_prs_v1`) call
log_search after a search completes. Failures to write are swallowed —
logging must never break the search path.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

_TRUTHY = {"1", "true", "yes", "on"}


def search_log_enabled() -> bool:
    """Return True when SEARCH_LOG is set to a truthy value."""
    return os.environ.get("SEARCH_LOG", "").strip().lower() in _TRUTHY


def log_search(query: str, result_count: int, source: str) -> None:
    """Append one search record when logging is enabled; no-op otherwise.

    Record format (one line):
        <ISO-8601 UTC>\t<source>\tresults=<N>\t<query with newlines escaped>

    Args:
        query: The raw search query text (logged verbatim — see privacy note).
        result_count: Number of results returned (post-threshold).
        source: "cli" or "mcp" — which surface served the search.
    """
    if not search_log_enabled():
        return
    path = os.environ.get("SEARCH_LOG_PATH", "senrah-search.log")
    line = (
        f"{datetime.now(timezone.utc).isoformat()}\t{source}\t"
        f"results={result_count}\t{query.replace(chr(10), ' ').replace(chr(13), ' ')}\n"
    )
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        # Logging must never break the search path (best-effort by design).
        pass
