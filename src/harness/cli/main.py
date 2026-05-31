"""
harness.cli.main — Typer application entry point.

Registers all harness subcommands:
  harness ingest   — ingest merged PRs from configured repositories
  harness index    — embed PRs into the skills table (Plan 01-03)
  harness search   — (Plan 01-04) semantic search over indexed PRs

The entry point in pyproject.toml points here:
  harness = "harness.cli.main:app"
"""

from __future__ import annotations

import typer

from harness.cli.ingest import ingest_cmd
from harness.cli.index import index_cmd

app = typer.Typer(
    help="Harness — semantic PR search for AI coding agents.",
    no_args_is_help=True,
)

# Ingest subcommand (Plan 01-02)
app.command("ingest")(ingest_cmd)

# Index subcommand (Plan 01-03)
app.command("index")(index_cmd)


# ---------------------------------------------------------------------------
# Placeholder subcommands (implemented in later plans)
# ---------------------------------------------------------------------------


@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Search query text"),
) -> None:
    """Semantic search over indexed PRs (implemented in Plan 01-04)."""
    typer.echo("harness search — not yet implemented (Plan 01-04).", err=True)
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
