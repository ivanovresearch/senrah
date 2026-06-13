"""
senrah.cli.serve — `senrah serve` command.

Starts the FastMCP server over stdio (default) or network (streamable-http).

Transport mapping:
  --transport stdio    → FastMCP.run(transport="stdio")
  --transport network  → FastMCP.run(transport="streamable-http") with stateless_http=True
  anything else        → typer.Exit(1) with an error message (SSE is forbidden)

Config loading follows the same error-handling pattern as cli/index.py:
  1. EnvSettings() — reads secrets from ENV; fails fast on missing required vars.
  2. find_config_file() — None → error+exit.
  3. load_yaml_config(path) — ValueError → error+exit.

Effective host/port:
  --host / --port flags override cfg.mcp.host / cfg.mcp.port (D-07).
  If neither is given, the values in senrah.yaml (McpConfig defaults) are used.

Design:
  - NO asyncio.run() wrap — FastMCP.run() calls anyio.run() internally.
  - NO event loop policy set here — cli/main.py sets WindowsSelectorEventLoopPolicy
    at import time; anyio.run() inherits it (RESEARCH Pattern 5/Pitfall 6).
  - All logs to stderr; stdout reserved for JSON-RPC on stdio transport (MCP-03).
  - SSE is explicitly rejected (CLAUDE.md / RESEARCH Pattern 4).

Security:
  - T-02-10: host defaults to "127.0.0.1" (D-07); --host 0.0.0.0 is explicit opt-in.
  - T-02-11: only "stdio" and "network" transport values accepted; all others rejected.
"""

from __future__ import annotations

from typing import Optional

import typer

from senrah.config import EnvSettings, find_config_file, load_yaml_config
from senrah.mcp.server import create_mcp_server


def serve_cmd(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        help="Transport to use: 'stdio' (default) or 'network' (streamable-http). SSE is not supported.",
    ),
    host: Optional[str] = typer.Option(
        None,
        "--host",
        help="Bind address for --transport network. Overrides senrah.yaml mcp.host (default: 127.0.0.1).",
    ),
    port: Optional[int] = typer.Option(
        None,
        "--port",
        help="Bind port for --transport network. Overrides senrah.yaml mcp.port (default: 8000).",
    ),
) -> None:
    """Start the Senrah MCP server.

    Default transport is stdio (for Claude Code / Codex agent integration).
    Use --transport network for a streamable-HTTP server bound to 127.0.0.1 by default.

    Config is read from senrah.yaml (non-secret tunables).
    Secrets (OPENAI_API_KEY, DATABASE_URL) are read from ENV / .env.

    Security note: --transport network binds 127.0.0.1 by default (D-07).
    Use --host 0.0.0.0 only when you intentionally expose to a shared network.
    """
    # Validate transport before loading config (fast failure on bad input)
    # T-02-11: only stdio and network accepted; SSE is explicitly forbidden (CLAUDE.md)
    if transport not in ("stdio", "network"):
        typer.echo(
            f"ERROR: --transport must be 'stdio' or 'network' (SSE is not supported). "
            f"Got: '{transport}'",
            err=True,
        )
        raise typer.Exit(code=1)

    # Load ENV secrets (follows exact error-handling structure from cli/index.py)
    try:
        env = EnvSettings()
    except Exception as exc:
        typer.echo(f"ERROR: Could not load secrets from ENV: {exc}", err=True)
        raise typer.Exit(code=1)

    # Load YAML config (non-secret tunables)
    cfg_path = find_config_file()
    if cfg_path is None:
        typer.echo(
            "ERROR: senrah.yaml not found. "
            "Create senrah.yaml in the project root (see senrah.yaml.example).",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        cfg = load_yaml_config(cfg_path)
    except ValueError as exc:
        typer.echo(f"ERROR: Invalid senrah.yaml: {exc}", err=True)
        raise typer.Exit(code=1)

    # Resolve effective host/port: CLI flags override YAML config (D-07).
    # Use `is not None` so an explicit `--port 0`/`--host ""` is honored rather
    # than silently falling back (0 and "" are falsy).
    effective_host = host if host is not None else cfg.mcp.host
    effective_port = port if port is not None else cfg.mcp.port

    # Map transport flag to FastMCP transport string
    # "stdio"   → "stdio"            (default; JSON-RPC over stdin/stdout)
    # "network" → "streamable-http"  (HTTP POST /mcp; stateless_http=True set in factory)
    mcp_transport = "stdio" if transport == "stdio" else "streamable-http"

    # OPS-04: maintain the status/heartbeat file while the server PROCESS runs
    # so `senrah status` can report up/down, transport, request count, latency.
    # A daemon THREAD (not the MCP lifespan) owns the heartbeat: for
    # streamable-http the lifespan is session-scoped and would leave the file
    # absent between client sessions.
    import threading

    from senrah.mcp.status import HEARTBEAT_SECONDS, McpStatusWriter

    status_writer = McpStatusWriter(cfg.mcp.status_file, transport=mcp_transport)
    status_writer.flush()
    stop_heartbeat = threading.Event()

    def _heartbeat() -> None:
        while not stop_heartbeat.wait(HEARTBEAT_SECONDS):
            status_writer.flush()

    threading.Thread(target=_heartbeat, daemon=True, name="mcp-status-heartbeat").start()

    # Build the server (host/port passed to the factory; stateless_http=True is always
    # set in create_mcp_server — this is correct for network and harmless for stdio)
    server = create_mcp_server(
        env, cfg, host=effective_host, port=effective_port, status_writer=status_writer
    )

    # Start the server — FastMCP.run() is synchronous (calls anyio.run() internally).
    # Do NOT wrap in asyncio.run() — that would nest event loop calls.
    # The WindowsSelectorEventLoopPolicy set in cli/main.py is inherited here.
    try:
        server.run(transport=mcp_transport)
    finally:
        # Clean shutdown: stop the heartbeat and remove the file so `status`
        # reports "down". On a crash/kill the file remains and `status`
        # reports "stale" once the heartbeat ages out — by design.
        stop_heartbeat.set()
        status_writer.remove()
