"""
senrah.cli.main — Typer application entry point.

Registers all senrah subcommands:
  senrah ingest   — ingest merged PRs from configured repositories
  senrah index    — embed PRs into the skills table (Plan 01-03)
  senrah search   — semantic search over indexed PRs (Plan 01-04)

The entry point in pyproject.toml points here:
  senrah = "senrah.cli.main:app"
"""

from __future__ import annotations

import asyncio
import sys

import typer

from senrah import __version__

# psycopg3 async cannot run on Windows' default ProactorEventLoop. The CLI uses
# an async connection pool (e.g. `senrah search`), so select the
# SelectorEventLoop on Windows before any asyncio.run() in a subcommand.
# No-op on non-Windows platforms.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from senrah.cli.index import index_cmd
from senrah.cli.ingest import ingest_cmd
from senrah.cli.init import init_cmd
from senrah.cli.repos import repos_cmd
from senrah.cli.search import search_cmd
from senrah.cli.serve import serve_cmd
from senrah.cli.status import status_cmd

app = typer.Typer(
    help="Senrah — semantic PR search for AI coding agents.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    """Print the version and exit (eager — runs before subcommand dispatch)."""
    if value:
        typer.echo(f"senrah {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the senrah version and exit.",
    ),
) -> None:
    """Senrah — semantic PR search for AI coding agents."""


# Init subcommand (Plan 03-04) — bootstrap/extend senrah.yaml with validation
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

# Status subcommand (Phase 5 / OPS-04) — ingest/index/MCP health view
app.command("status")(status_cmd)


if __name__ == "__main__":
    app()
