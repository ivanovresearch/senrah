"""
harness.cli.index — `harness index` command.

Composition root for the index pipeline:
- Loads EnvSettings (secrets from ENV) and YAML config (tunables).
- Resolves the configured project + repository to get a repository_id.
- Calls Indexer.run(repository_id) via asyncio.run().

There is no --reindex flag in Phase 1 (deferred to Phase 4 per CONTEXT.md).

Security:
- T-03-01: OPENAI_API_KEY read from ENV only (via AsyncOpenAI / EnvSettings).
           Never logged, never in config.
"""

from __future__ import annotations

import asyncio
import sys

import typer

from harness.config import EnvSettings, find_config_file, load_yaml_config
from harness.db.models import Project, Repository
from harness.db.pool import connect_sync
from harness.db.repos.project import ProjectRepo
from harness.db.repos.repository import RepositoryRepo
from harness.indexer.index import Indexer


def index_cmd() -> None:
    """Embed ingested PRs into the skills table.

    Reads problem text (title + body) and solution text (diff) for each
    unindexed pull request, truncates to token limits (D-06/D-07), and writes
    both embeddings to the skills table with the configured model and version
    persisted per row (D-08).

    Config is read from harness.yaml (non-secret tunables).
    Secrets (OPENAI_API_KEY, DATABASE_URL) are read from ENV / .env.
    """
    # Load ENV secrets (T-03-01: key from ENV only)
    try:
        env = EnvSettings()
    except Exception as exc:
        typer.echo(f"ERROR: Could not load secrets from ENV: {exc}", err=True)
        raise typer.Exit(code=1)

    # Load YAML config (non-secret tunables)
    cfg_path = find_config_file()
    if cfg_path is None:
        typer.echo(
            "ERROR: harness.yaml not found. "
            "Create harness.yaml in the project root (see harness.yaml.example).",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        cfg = load_yaml_config(cfg_path)
    except ValueError as exc:
        typer.echo(f"ERROR: Invalid harness.yaml: {exc}", err=True)
        raise typer.Exit(code=1)

    if not cfg.repositories:
        typer.echo(
            "ERROR: No repositories configured in harness.yaml. "
            "Add at least one entry under 'repositories:'.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Run index for each configured repository (D-05: {type, name} addressing)
    with connect_sync(env.database_url) as conn:
        project_repo_accessor = ProjectRepo(conn)
        repo_accessor = RepositoryRepo(conn)

        for repo_cfg in cfg.repositories:
            repo_name = repo_cfg.get("name", "")
            repo_type = repo_cfg.get("type", "github")

            if not repo_name:
                typer.echo(
                    "WARNING: Skipping repository with no 'name' in config.",
                    err=True,
                )
                continue

            # Resolve repository_id — the project + repository rows must exist
            # (created by `harness ingest`).
            project_name = cfg.project_name or repo_name.split("/")[0]
            project = project_repo_accessor.get_by_name(project_name)
            if project is None:
                typer.echo(
                    f"ERROR: Project '{project_name}' not found in database. "
                    "Run 'harness ingest' first to create the project and ingest PRs.",
                    err=True,
                )
                raise typer.Exit(code=1)

            repository = repo_accessor.get(project.id, repo_name)  # type: ignore[arg-type]
            if repository is None:
                typer.echo(
                    f"ERROR: Repository '{repo_name}' not found in database. "
                    "Run 'harness ingest' first to ingest PRs.",
                    err=True,
                )
                raise typer.Exit(code=1)

            repository_id: int = repository.id  # type: ignore[assignment]

            typer.echo(f"Indexing {repo_name} (model: {cfg.embed.model} / {cfg.embed.version})...")

            try:
                indexer = Indexer(conn, cfg.embed)
                count = asyncio.run(indexer.run(repository_id))
                typer.echo(f"Done: {count} PR(s) indexed for {repo_name}.")
            except Exception as exc:
                typer.echo(
                    f"ERROR indexing {repo_name}: {exc}",
                    err=True,
                )
                raise typer.Exit(code=1)
