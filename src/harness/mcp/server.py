"""
harness.mcp.server — FastMCP server factory for the search_prs_v1 tool.

Provides:
- create_mcp_server(env, cfg, host=None, port=None) -> FastMCP
  Factory function (NOT a module-level singleton) that constructs a FastMCP
  instance with a lifespan-managed async pool and registers the search_prs_v1 tool.
  Calling code (serve_cmd) passes EnvSettings + YamlConfig; tests may pass None
  for both to get a test-safe instance with default configs and a stub pool.

Design principles:
- NO SQL, NO print() — all logging via logger.* (stderr only, MCP-03).
- Factory pattern: no module-level FastMCP singleton (Open Question 2 RESOLVED).
  Side-effect-free import; testable via dependency injection.
- Lifespan: pool opened once at server startup, closed at shutdown (not per-request).
- Dual output (D-01 / Path B): returns Annotated[CallToolResult, SearchResponseV1]
  so FastMCP generates outputSchema AND we control the text content block.
- Error masking (D-05/D-06): generic ToolError messages to client, full detail to
  stderr via logger.error. DSN never leaks into the MCP response.

Security:
- T-02-05: ToolError messages are generic — no DSN, API key, or stack internals.
- T-02-06: query text is embedded to a vector; only the vector reaches SkillRepo.
- T-02-07: No print() — all output via logger (stderr) or tool content (stdout
           reserved for JSON-RPC on stdio transport).
- T-02-08: limit clamped to min(limit, 20) — prevents oversized result payloads.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from harness.db.repos.skill import SkillRepo
from harness.indexer.embedder import embed_texts
from harness.mcp.formatting import build_envelope, render_text_response
from harness.mcp.schema import SearchResponseV1

if TYPE_CHECKING:
    from harness.config import EnvSettings, YamlConfig
    from harness.mcp.status import McpStatusWriter

logger = logging.getLogger(__name__)

# Absolute cap on `limit` parameter to prevent oversized payloads (T-02-08).
_MAX_LIMIT = 20


def _redact_credentials(message: str) -> str:
    """Strip connection-string credentials/DSNs from a message before logging (D-06).

    psycopg OperationalError messages can embed the full DATABASE_URL — including the
    password — in their text. Redact URI userinfo and libpq ``password=`` fields so
    secrets never reach the stderr log even though the client only ever sees the
    generic ToolError message.
    """
    redacted = re.sub(r"(\w+://)[^/\s@]+@", r"\1***@", message)
    redacted = re.sub(r"(?i)(password=)\S+", r"\1***", redacted)
    return redacted

# ---------------------------------------------------------------------------
# Default config values used when cfg=None (unit-test path)
# ---------------------------------------------------------------------------
_DEFAULT_TOP_N = 5
_DEFAULT_SCORE_THRESHOLD = 0.40
_DEFAULT_OVERSAMPLE_FACTOR = 5
_DEFAULT_PROBLEM_WEIGHT = 0.6
_DEFAULT_SOLUTION_WEIGHT = 0.4
_DEFAULT_OUTPUT_DIFF_LIMIT = 2000
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000
_DEFAULT_LOG_LEVEL = "WARNING"
_DEFAULT_EMBED_MODEL = "text-embedding-3-small"


# ---------------------------------------------------------------------------
# Test-stub pool (used when env=None in unit tests)
# ---------------------------------------------------------------------------

class _StubConnection:
    """Minimal async connection stub for unit tests where SkillRepo.search is mocked."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def cursor(self):
        from unittest.mock import AsyncMock
        return AsyncMock()


class _StubPool:
    """Minimal pool stub for unit tests — never actually opens a DB connection."""

    def connection(self):
        return _StubConnection()

    async def close(self):
        pass


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_mcp_server(
    env: Optional["EnvSettings"],
    cfg: Optional["YamlConfig"],
    host: Optional[str] = None,
    port: Optional[int] = None,
    status_writer: Optional["McpStatusWriter"] = None,
) -> FastMCP:
    """Construct a FastMCP instance with search_prs_v1 registered.

    This is a FACTORY — not a module-level singleton. Call once from serve_cmd
    (or from tests with env=None/cfg=None for unit testing).

    When env=None, the lifespan uses a stub pool (SkillRepo.search must be
    patched in tests). When env is provided, the lifespan opens a real
    AsyncConnectionPool via create_pool(env.database_url).

    Args:
        env: EnvSettings with DATABASE_URL and OPENAI_API_KEY. None = test mode.
        cfg: YamlConfig with search/embed/mcp blocks. None = use defaults.
        host: Override cfg.mcp.host (or _DEFAULT_HOST). D-07: 127.0.0.1.
        port: Override cfg.mcp.port (or _DEFAULT_PORT).

    Returns:
        FastMCP instance with search_prs_v1 tool registered and lifespan set.
    """
    # Resolve effective host/port/log_level from cfg or defaults
    effective_host = host if host is not None else (cfg.mcp.host if cfg else _DEFAULT_HOST)
    effective_port = port if port is not None else (cfg.mcp.port if cfg else _DEFAULT_PORT)
    effective_log_level = cfg.mcp.log_level if cfg else _DEFAULT_LOG_LEVEL

    @asynccontextmanager
    async def _lifespan(server: FastMCP):
        """Open async pool at startup; close at shutdown (Pattern 3).

        NOTE: the OPS-04 status-file heartbeat does NOT live here — for
        streamable-http this lifespan is session-scoped, not process-scoped
        (it would only run while a client session is open). The heartbeat
        thread is owned by serve_cmd; the status_writer passed to the factory
        is used only to record per-request latency in the tool handler.
        """
        if env is not None:
            from harness.db.pool import create_pool
            pool = await create_pool(env.database_url)
        else:
            # Test path: use stub pool — SkillRepo.search is patched by the test
            pool = _StubPool()
        try:
            yield {"pool": pool, "settings": env, "cfg": cfg}
        finally:
            await pool.close()

    mcp = FastMCP(
        "harness",
        lifespan=_lifespan,
        host=effective_host,
        port=effective_port,
        stateless_http=True,
        log_level=effective_log_level,
    )

    # -----------------------------------------------------------------------
    # Tool registration — inside factory so lifespan closure captures env/cfg
    # -----------------------------------------------------------------------

    @mcp.tool(name="search_prs_v1")
    async def search_prs_v1(
        query: str,
        repos: Optional[list[str]] = None,
        limit: int = (_DEFAULT_TOP_N if cfg is None else cfg.search.top_n),
        debug: bool = False,
        ctx: Context = ...,  # type: ignore[assignment]
    ) -> Annotated[CallToolResult, SearchResponseV1]:
        """Search indexed pull requests for precedents matching `query`.

        Returns a dual structured+text response (D-01 / Path B):
        - structuredContent: SearchResponseV1 (validated Pydantic dict, MCP-02 outputSchema)
        - content[0]: Human-readable text block with calibrated confidence labels

        Args:
            query: Natural-language search query (required).
            repos: Optional list of "owner/repo" strings to narrow results (SEARCH-03).
                   When omitted, searches all indexed repos.
            limit: Max results to return (default = config top_n; clamped to 20).
            debug: When True, include p_sim/s_sim score components in output (MCP-02).
            ctx: FastMCP context (injected by framework).

        Returns:
            CallToolResult with structuredContent=SearchResponseV1 dict and prose text.

        Raises:
            ToolError: On query embedding failure (D-05) or DB/search error (D-06).
                       Messages are generic — no DSN or internal details (T-02-05).
        """
        import time as _time

        _t0 = _time.monotonic()  # OPS-04: request latency for the status file

        # Resolve per-call config from lifespan context or module defaults
        lifespan_ctx = ctx.request_context.lifespan_context
        pool = lifespan_ctx["pool"]
        call_cfg = lifespan_ctx["cfg"]
        call_env = lifespan_ctx["settings"]

        # Config resolution: lifespan cfg → defaults
        top_n_default = call_cfg.search.top_n if call_cfg else _DEFAULT_TOP_N
        score_threshold = call_cfg.search.score_threshold if call_cfg else _DEFAULT_SCORE_THRESHOLD
        oversample_factor = call_cfg.search.oversample_factor if call_cfg else _DEFAULT_OVERSAMPLE_FACTOR
        problem_weight = call_cfg.search.problem_weight if call_cfg else _DEFAULT_PROBLEM_WEIGHT
        solution_weight = call_cfg.search.solution_weight if call_cfg else _DEFAULT_SOLUTION_WEIGHT
        output_diff_limit = call_cfg.mcp.output_diff_limit if call_cfg else _DEFAULT_OUTPUT_DIFF_LIMIT
        embed_model = call_cfg.embed.model if call_cfg else _DEFAULT_EMBED_MODEL
        embed_base_url = call_cfg.embed.base_url if call_cfg else None
        api_key = call_env.openai_api_key if call_env else None

        # Clamp limit to [1, cap] (T-02-08). Lower bound prevents limit<=0 from
        # silently returning [] (results[:0]) or producing a negative SQL LIMIT.
        effective_limit = max(1, min(limit, _MAX_LIMIT))

        # Step 1: Embed the query (D-05: failure → generic ToolError)
        try:
            query_vecs = await embed_texts(
                [query],
                model=embed_model,
                api_key=api_key,
                base_url=embed_base_url,
            )
        except Exception as exc:
            logger.error("Query embedding failed: %s", _redact_credentials(str(exc)))
            raise ToolError(
                "Query embedding failed — check OPENAI_API_KEY and service availability"
            )

        query_vec = query_vecs[0]

        # Step 2: ANN search via SkillRepo (D-06: failure → generic ToolError)
        try:
            async with pool.connection() as conn:
                repo = SkillRepo(conn)
                results = await repo.search(
                    query_vec=query_vec,
                    top_n=effective_limit,
                    oversample_factor=oversample_factor,
                    score_threshold=score_threshold,
                    problem_weight=problem_weight,
                    solution_weight=solution_weight,
                    repos=repos or None,
                )

                best = None
                if not results:
                    # D-02 / Pattern 8: re-query at threshold=0.0 for best_below_threshold
                    all_candidates = await repo.search(
                        query_vec=query_vec,
                        top_n=1,
                        oversample_factor=oversample_factor,
                        score_threshold=0.0,
                        problem_weight=problem_weight,
                        solution_weight=solution_weight,
                        repos=repos or None,
                    )
                    best = all_candidates[0] if all_candidates else None
        except ToolError:
            raise  # already a ToolError — don't re-wrap
        except Exception as exc:
            # Never log DSN — psycopg exceptions may include connection string (D-06).
            # _redact_credentials strips userinfo/password before it reaches stderr.
            logger.error("DB search error: %s", _redact_credentials(str(exc)))
            raise ToolError("Search backend unavailable")

        # OPS-05: opt-in search logging (SEARCH_LOG=true); no-op by default.
        from harness.search_log import log_search

        log_search(query, len(results), source="mcp")

        # OPS-04: record request latency into the status file (serve mode only).
        if status_writer is not None:
            status_writer.record_request((_time.monotonic() - _t0) * 1000.0)

        # Step 3: Build response envelope and text block
        envelope = build_envelope(
            results,
            best,
            debug=debug,
            output_diff_limit=output_diff_limit,
        )
        text = render_text_response(envelope, debug=debug)

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=envelope.model_dump(mode="json"),
        )

    return mcp
