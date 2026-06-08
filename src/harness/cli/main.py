"""
harness.cli.main — Typer application entry point.

Registers all harness subcommands:
  harness ingest   — ingest merged PRs from configured repositories
  harness index    — embed PRs into the skills table (Plan 01-03)
  harness search   — semantic search over indexed PRs (Plan 01-04)

The entry point in pyproject.toml points here:
  harness = "harness.cli.main:app"
"""

from __future__ import annotations

import asyncio
import sys

import typer

# psycopg3 async cannot run on Windows' default ProactorEventLoop. The CLI uses
# an async connection pool (e.g. `harness search`), so select the
# SelectorEventLoop on Windows before any asyncio.run() in a subcommand.
# No-op on non-Windows platforms.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from harness.cli.index import index_cmd
from harness.cli.ingest import ingest_cmd
from harness.cli.init import init_cmd
from harness.cli.repos import repos_cmd
from harness.cli.search import search_cmd
from harness.cli.serve import serve_cmd

app = typer.Typer(
    help="Harness — semantic PR search for AI coding agents.",
    no_args_is_help=True,
)

# Init subcommand (Plan 03-04) — bootstrap/extend harness.yaml with validation
app.command("init")(init_cmd)

# Ingest subcommand (Plan 01-02)
app.command("ingest")(ingest_cmd)

# Index subcommand (Plan 01-03)
app.command("index")(index_cmd)

# Search subcommand (Plan 01-04)
app.command("search")(search_cmd)

# Serve subcommand (Plan 02-03) — start the MCP server over stdio or network
app.command("serve")(serve_cmd)

# Repos subcommand (Plan 03-05) — read-only list of repos + scope + op-state
app.command("repos")(repos_cmd)


if __name__ == "__main__":
    app()
