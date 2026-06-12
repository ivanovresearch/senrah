"""
harness.cli.init — `harness init` command (OPS-01).

Re-runnable, comment-preserving project/repo bootstrap with live credential
validation. Prompts for a project name + repository + scope, validates the
token via a live test-read on the entered repo, and merges the entry into
harness.yaml (preserving comments and the embed/search/mcp blocks) only after
the credential is accepted.

This is a composition root: the ONE place (besides cli/ingest.py) where the
concrete GitHubConnector is built.

Security:
- T-02-01: Token read only from ENV via EnvSettings; never echoed.
- T-OPS01-1: validate_credentials raises a token-free message on reject; init
  never passes the token to typer.echo.
- T-OPS01-2: upsert_repo_entry re-runs _check_for_secrets on the serialized
  output before writing (Plan 01).
"""

from __future__ import annotations

from pathlib import Path

import typer

from harness.config import (
    EnvSettings,
    Scope,
    find_config_file,
    load_yaml_config,
    upsert_repo_entry,
)
from harness.connectors.github import GitHubConnector


# Repository types with an implemented connector. Validated at prompt time:
# live UAT (2026-06-12) showed init silently stored an arbitrary type (and a
# UTF-8 BOM pasted with the input), producing a config entry no connector
# can serve.
_KNOWN_REPO_TYPES = ("github",)


def _clean(value: str) -> str:
    """Normalize prompt input: strip whitespace and any UTF-8 BOM chars."""
    return value.replace("\ufeff", "").strip()


def _prompt_scope() -> Scope:
    """Prompt for an ingest scope (D-A3): mode + value coerced per mode."""
    mode = _clean(
        typer.prompt(
            "Scope mode (all / last_n / since_date / period)", default="last_n"
        )
    )
    if mode == "all":
        return Scope(mode="all", value=None)
    if mode == "last_n":
        return Scope(
            mode="last_n",
            value=typer.prompt("Number of newest merged PRs", type=int, default=100),
        )
    if mode == "since_date":
        return Scope(
            mode="since_date", value=_clean(typer.prompt("Since date (YYYY-MM-DD)"))
        )
    if mode == "period":
        return Scope(mode="period", value=_clean(typer.prompt("Period (e.g. 90d)")))
    typer.echo(
        f"ERROR: unknown scope mode {mode!r} (use all/last_n/since_date/period).",
        err=True,
    )
    raise typer.Exit(code=1)


def init_cmd() -> None:
    """Initialize / extend harness.yaml: add a repository with a scope.

    Prompts for the project name (first run only), the repository, and the
    ingest scope, validates the GitHub credential via a live test-read on the
    entered repository, and merges the entry into harness.yaml — preserving
    existing comments and the embed/search/mcp blocks. Re-running is an upsert.
    """
    # Load ENV secrets (T-02-01: token from ENV only)
    try:
        env = EnvSettings()
    except Exception as exc:
        typer.echo(f"ERROR: Could not load secrets from ENV: {exc}", err=True)
        raise typer.Exit(code=1)

    # Resolve the target harness.yaml (existing one, or a new file in cwd)
    cfg_path = find_config_file() or (Path.cwd() / "harness.yaml")

    # Project name: prompt only when not already set (D-A2 re-runnable upsert)
    existing_project: str | None = None
    if cfg_path.exists():
        try:
            existing_project = load_yaml_config(cfg_path).project_name or None
        except ValueError as exc:
            typer.echo(f"ERROR: Invalid harness.yaml: {exc}", err=True)
            raise typer.Exit(code=1)

    project_name = existing_project or _clean(typer.prompt("Project name"))

    repo_type = _clean(typer.prompt("Repository type", default="github"))
    if repo_type not in _KNOWN_REPO_TYPES:
        typer.echo(
            f"ERROR: unknown repository type {repo_type!r} "
            f"(supported: {', '.join(_KNOWN_REPO_TYPES)}).",
            err=True,
        )
        raise typer.Exit(code=1)
    repo_name = _clean(typer.prompt("Repository (owner/repo)"))
    scope = _prompt_scope()

    # Validate the credential via a live test-read on the entered repo (OPS-01,
    # Open Question 2). Composition root — the ONE place the connector is built.
    connector = GitHubConnector(env.github_token)
    try:
        connector.validate_credentials(repo_full_name=repo_name)
    except Exception as exc:
        # Token-free reject message (validate_credentials never echoes the token).
        typer.echo(f"REJECTED: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo(
        f"OK: token validated for {repo_name} (read access confirmed).", err=True
    )

    # Merge the entry into harness.yaml only AFTER acceptance (D-A2).
    upsert_repo_entry(
        cfg_path,
        repo_name=repo_name,
        repo_type=repo_type,
        scope=scope,
        project_name=project_name,
    )
    scope_desc = scope.mode if scope.value is None else f"{scope.mode} {scope.value}"
    typer.echo(f"Wrote {repo_name} (scope: {scope_desc}) to {cfg_path}.", err=True)
