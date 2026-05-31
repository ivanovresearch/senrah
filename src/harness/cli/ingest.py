"""
harness.cli.ingest — `harness ingest` command.

This is the composition root for the ingest pipeline:
- The ONE place where the concrete GitHubConnector is instantiated.
- Loads EnvSettings (secrets from ENV) and YAML config (tunables).
- Resolves the configured repository (D-05: {type, name} from config).
- Calls Ingester.run(connector, repo, project, last_n).

CLI flags per D-04:
  --last-n N    fetch exactly N merged PRs (overrides config default_last_n)
  --all         fetch full history (last_n=None)
  (no flag)     use config default_last_n (default 100)

Security:
- T-02-01: Token read only from ENV via EnvSettings; never logged.
- The connector is created here and passed by protocol to Ingester.
"""

from __future__ import annotations

import sys
from typing import Optional

import typer

from harness.config import EnvSettings, YamlConfig, find_config_file, load_yaml_config
from harness.connectors.github import GitHubConnector
from harness.db.pool import connect_sync
from harness.ingester.ingest import Ingester


def ingest_cmd(
    last_n: Optional[int] = typer.Option(
        None,
        "--last-n",
        help="Fetch exactly N merged PRs (overrides config default_last_n).",
        min=1,
    ),
    all_prs: bool = typer.Option(
        False,
        "--all",
        help="Fetch full PR history (no count limit).",
    ),
) -> None:
    """Ingest merged PRs from the configured GitHub repository.

    Reads repository config from harness.yaml (walked up from cwd).
    Secrets (GITHUB_TOKEN, DATABASE_URL) are read from ENV / .env.

    Count flags (D-04):
    \\b
      --last-n N   fetch exactly N merged PRs
      --all        fetch all merged PRs (full history)
      (neither)    use ingest.default_last_n from harness.yaml (default: 100)
    """
    # Load ENV secrets (T-02-01: token from ENV only)
    try:
        env = EnvSettings()
    except Exception as exc:
        typer.echo(
            f"ERROR: Could not load secrets from ENV: {exc}", err=True
        )
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

    # Validate config has repositories
    if not cfg.repositories:
        typer.echo(
            "ERROR: No repositories configured in harness.yaml. "
            "Add at least one entry under 'repositories:'.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Resolve last_n from flags / config (D-04)
    if all_prs:
        resolved_last_n: int | None = None  # no limit
    elif last_n is not None:
        resolved_last_n = last_n
    else:
        resolved_last_n = cfg.default_last_n  # default 100

    # Build connector (composition root — the ONE place concrete connector is created)
    connector = GitHubConnector(env.github_token)

    # Run ingest for each configured repository (D-05: {type, name} addressing)
    with connect_sync(env.database_url) as conn:
        ingester = Ingester(conn)
        for repo_cfg in cfg.repositories:
            repo_type = repo_cfg.get("type", "github")
            repo_name = repo_cfg.get("name", "")

            if not repo_name:
                typer.echo(
                    "WARNING: Skipping repository with no 'name' in config.",
                    err=True,
                )
                continue

            typer.echo(
                f"Ingesting {repo_name} "
                f"({'all' if resolved_last_n is None else resolved_last_n} PRs)..."
            )
            try:
                count = ingester.run(
                    connector=connector,
                    repo_full_name=repo_name,
                    project_name=cfg.project_name or repo_name.split("/")[0],
                    repo_type=repo_type,
                    last_n=resolved_last_n,
                )
                typer.echo(f"Done: {count} PR(s) upserted for {repo_name}.")
            except Exception as exc:
                typer.echo(
                    f"ERROR ingesting {repo_name}: {exc}",
                    err=True,
                )
                raise typer.Exit(code=1)
