"""
harness.cli.main — Typer application entry point.

Registers all harness subcommands:
  harness ingest   — ingest merged PRs from configured repositories
  harness index    — (Plan 01-03) embed PRs into the skills table
  harness search   — (Plan 01-04) semantic search over indexed PRs

The entry point in pyproject.toml points here:
  harness = "harness.cli.main:app"
"""

from __future__ import annotations

import typer

from harness.cli.ingest import ingest_cmd

app = typer.Typer(
    help="Harness — semantic PR search for AI coding agents.",
    no_args_is_help=True,
)

# Ingest subcommand (Plan 01-02)
app.command("ingest")(ingest_cmd)


# ---------------------------------------------------------------------------
# Placeholder subcommands (implemented in later plans)
# These are registered lazily so that `harness --help` shows them, but
# the import of the actual implementation is deferred until the command runs.
# ---------------------------------------------------------------------------


@app.command("index")
def index_cmd() -> None:
    """Embed ingested PRs into the skills table (implemented in Plan 01-03)."""
    typer.echo("harness index — not yet implemented (Plan 01-03).", err=True)
    raise typer.Exit(code=1)


@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Search query text"),
) -> None:
    """Semantic search over indexed PRs (implemented in Plan 01-04)."""
    typer.echo("harness search — not yet implemented (Plan 01-04).", err=True)
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
