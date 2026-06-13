"""
senrah.cli.repos — `senrah repos` command (OPS-02).

Read-only listing that joins the authoritative YAML repo list + scope (D-A1)
with the DB operational state (cursor, last-run, status). Repos that have a
YAML entry but no DB op-state row yet show "(never run)".

No writes (D-A2) — no connector is constructed and no write SQL is issued; the
only DB access is the parameterized SELECTs in ProjectRepo.get_by_name and
RepositoryRepo.get_op_state.
"""

from __future__ import annotations

import typer

from senrah.config import (
    EnvSettings,
    Scope,
    find_config_file,
    load_yaml_config,
)
from senrah.db.models import RepoOpState
from senrah.db.pool import connect_sync
from senrah.db.repos.project import ProjectRepo
from senrah.db.repos.repository import RepositoryRepo

_NEVER = "(never run)"

# Column widths for the plain-text table (structural ASCII only).
_W_NAME = 30
_W_SCOPE = 16
_W_CURSOR = 24
_W_LASTRUN = 18


def _scope_desc(scope: Scope | None) -> str:
    """Render a scope as 'mode' or 'mode=value'."""
    if scope is None:
        return "-"
    return scope.mode if scope.value is None else f"{scope.mode}={scope.value}"


def _effective_scope(repo_cfg: dict, default_scope: Scope) -> Scope:
    """Per-repo scope when present, else the resolved default_scope (D-A3)."""
    raw = repo_cfg.get("scope")
    if isinstance(raw, dict) and raw.get("mode"):
        return Scope(mode=raw["mode"], value=raw.get("value"))
    return default_scope


def _format_op_state_row(
    repo_name: str,
    op_state: RepoOpState | None,
    scope: Scope | None = None,
) -> str:
    """Format one table row: repo | scope | cursor | last run | status.

    A None op_state (no DB row yet) renders "(never run)" for the operational
    cells rather than erroring (OPS-02).
    """
    scope_s = _scope_desc(scope)
    if op_state is None:
        cursor_s = last_run_s = status_s = _NEVER
    else:
        if op_state.cursor_merged_at is not None:
            cursor_s = op_state.cursor_merged_at.strftime("%Y-%m-%d")
            if op_state.cursor_number:
                cursor_s += f" (#{op_state.cursor_number})"
        else:
            cursor_s = "-"
        last_run_s = (
            op_state.last_run_at.strftime("%Y-%m-%d %H:%M")
            if op_state.last_run_at is not None
            else "-"
        )
        status_s = op_state.last_run_status or "-"
    return (
        f"{repo_name:<{_W_NAME}}  {scope_s:<{_W_SCOPE}}  "
        f"{cursor_s:<{_W_CURSOR}}  {last_run_s:<{_W_LASTRUN}}  {status_s}"
    )


def _format_header() -> str:
    return (
        f"{'REPOSITORY':<{_W_NAME}}  {'SCOPE':<{_W_SCOPE}}  "
        f"{'CURSOR (merged_at)':<{_W_CURSOR}}  {'LAST RUN':<{_W_LASTRUN}}  STATUS"
    )


def repos_cmd() -> None:
    """List configured repositories with ingest scope, cursor, and last-run status.

    The repo list + scope are read from senrah.yaml (authoritative, D-A1); the
    operational state is JOINed from the DB per repo. Read-only.
    """
    # Load ENV secrets (mirrors the ingest preamble). Only database_url is used.
    try:
        env = EnvSettings()
    except Exception as exc:
        typer.echo(f"ERROR: Could not load secrets from ENV: {exc}", err=True)
        raise typer.Exit(code=1)

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

    if not cfg.repositories:
        typer.echo("No repositories configured in senrah.yaml.", err=True)
        return

    with connect_sync(env.database_url) as conn:
        # Resolve project_id; a missing project row means nothing has been
        # ingested yet → every repo shows "(never run)".
        project = (
            ProjectRepo(conn).get_by_name(cfg.project_name)
            if cfg.project_name
            else None
        )
        project_id = project.id if project is not None else None
        repo_repo = RepositoryRepo(conn)

        typer.echo(_format_header())
        for repo_cfg in cfg.repositories:
            name = repo_cfg.get("name", "")
            if not name:
                continue
            scope = _effective_scope(repo_cfg, cfg.default_scope)
            op_state = (
                repo_repo.get_op_state(project_id, name)
                if project_id is not None
                else None
            )
            typer.echo(_format_op_state_row(name, op_state, scope))
