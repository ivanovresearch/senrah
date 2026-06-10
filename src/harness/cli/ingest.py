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

from typing import Optional

import typer

from harness.config import (
    EnvSettings,
    Scope,
    find_config_file,
    load_yaml_config,
)
from harness.connectors.github import GitHubConnector
from harness.db.pool import connect_sync
from harness.ingester.ingest import Ingester


def _coerce_scope_value(mode: str, value: str) -> object:
    """Coerce a CLI --scope VALUE string to the type expected per mode."""
    if mode == "all":
        return None
    if mode == "last_n":
        return int(value)
    # since_date ("YYYY-MM-DD") and period ("90d") stay as strings
    return value


def _resolve_scope(
    cli_scope: Optional[tuple[str, str]],
    repo_cfg: dict,
    default_scope: Scope,
) -> Scope:
    """Scope precedence (D-A3): CLI --scope > per-repo scope > default_scope."""
    if cli_scope is not None:
        mode, value = cli_scope
        return Scope(mode=mode, value=_coerce_scope_value(mode, value))
    repo_scope = repo_cfg.get("scope")
    if isinstance(repo_scope, dict) and repo_scope.get("mode"):
        return Scope(mode=repo_scope["mode"], value=repo_scope.get("value"))
    return default_scope


def ingest_cmd(
    last_n: Optional[int] = typer.Option(
        None,
        "--last-n",
        help="Fetch exactly N merged PRs (back-compat; maps to a last_n scope).",
        min=1,
    ),
    all_prs: bool = typer.Option(
        False,
        "--all",
        help="Fetch full PR history (maps to an 'all' scope).",
    ),
    scope: Optional[tuple[str, str]] = typer.Option(
        None,
        "--scope",
        help="Ingest scope as MODE VALUE (e.g. --scope last_n 200, "
        "--scope period 90d, --scope since_date 2024-01-01, --scope all -).",
        metavar="MODE VALUE",
    ),
    backfill: bool = typer.Option(
        False,
        "--backfill",
        help="Deprecated/no-op: every run already re-scans the scope window "
        "(the cursor never bounds traversal). Use '--scope all -' for a deep "
        "re-enumeration.",
    ),
) -> None:
    """Ingest merged PRs from the configured GitHub repository.

    Reads repository config from harness.yaml (walked up from cwd).
    Secrets (GITHUB_TOKEN, DATABASE_URL) are read from ENV / .env.

    Scope (D-A3, precedence: CLI --scope > per-repo scope > ingest.default_scope):
    \\b
      --scope last_n 200        newest 200 merged PRs
      --scope period 90d        merged in the last 90 days
      --scope since_date DATE   merged on/after an ISO date
      --scope all -             full history
      --last-n N / --all        back-compat shortcuts (map to a last_n / all scope)
      --backfill                deprecated/no-op (every run re-scans the scope window)
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

    # Build the CLI-level scope override (precedence: --scope > --all > --last-n;
    # None falls through to per-repo scope / default_scope per repo).
    if scope is not None:
        cli_scope: Optional[tuple[str, str]] = scope
    elif all_prs:
        cli_scope = ("all", "-")
    elif last_n is not None:
        cli_scope = ("last_n", str(last_n))
    else:
        cli_scope = None

    # Build connector (composition root — the ONE place concrete connector is created)
    connector = GitHubConnector(env.github_token)

    # Run ingest for each configured repository (D-05: {type, name} addressing).
    # autocommit=True so the Ingester's per-PR conn.transaction() COMMITS durably
    # (D-B3 resume guarantee); otherwise per-PR blocks degrade to savepoints and a
    # crash loses all progress.
    with connect_sync(env.database_url, autocommit=True) as conn:
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

            repo_scope = _resolve_scope(cli_scope, repo_cfg, cfg.default_scope)
            typer.echo(
                f"Ingesting {repo_name} "
                f"(scope: {repo_scope.mode}"
                f"{'' if repo_scope.value is None else f' {repo_scope.value}'}"
                f"{', backfill' if backfill else ''})..."
            )
            try:
                count = ingester.run(
                    connector=connector,
                    repo_full_name=repo_name,
                    project_name=cfg.project_name or repo_name.split("/")[0],
                    repo_type=repo_type,
                    scope=repo_scope,
                    backfill=backfill,
                    filters=cfg.filters,
                )
                typer.echo(f"Done: {count} PR(s) upserted for {repo_name}.")
            except Exception as exc:
                typer.echo(
                    f"ERROR ingesting {repo_name}: {exc}",
                    err=True,
                )
                raise typer.Exit(code=1)
