"""
harness.cli.status — `harness status` command (OPS-04).

Three sections:
- INGEST: per-repo PR counts, diagnostic cursor, last-run status, the most
  recent run's errored-PR list, and live GitHub rate-limit remaining/reset
  (one call to the rate_limit endpoint — it does not consume quota).
- INDEX: total vector rows, embedding model/version breakdown, count of raw
  PRs lacking embeddings, last index timestamp.
- MCP: up/down (heartbeat-file freshness), transport, request count,
  p50/p90 latency — read from the status file `harness serve` maintains.

Composition root pattern mirrors cli/repos.py: config + env loaded here,
all DB access through the repos layer (no SQL in this module).
"""

from __future__ import annotations

from datetime import datetime, timezone

import typer

from harness.config import EnvSettings, find_config_file, load_yaml_config
from harness.db.pool import connect_sync
from harness.db.repos.pr import PRRepo
from harness.db.repos.project import ProjectRepo
from harness.db.repos.repository import RepositoryRepo
from harness.db.repos.skill import SkillRepo
from harness.mcp.status import HEARTBEAT_SECONDS, read_status


def _fmt_ts(ts) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S %Z") if ts else "-"


def status_cmd() -> None:
    """Show ingest / index / MCP health across the project."""
    try:
        env = EnvSettings()
    except Exception as exc:
        typer.echo(f"ERROR: Could not load secrets from ENV: {exc}", err=True)
        raise typer.Exit(code=1)

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

    # ---------------- INGEST ----------------
    typer.echo("=== INGEST ===")
    rate_line = "rate limit: (no GITHUB_TOKEN or API unreachable)"
    try:
        from harness.connectors.github import GitHubConnector

        rate = GitHubConnector(env.github_token).rate_limit_status()
        rate_line = (
            f"rate limit: {rate.remaining}/{rate.limit} remaining, "
            f"resets {_fmt_ts(rate.reset_at)}"
        )
    except Exception as exc:  # status must render even when GitHub is down
        rate_line = f"rate limit: unavailable ({type(exc).__name__})"
    typer.echo(rate_line)

    with connect_sync(env.database_url) as conn:
        project_repo = ProjectRepo(conn)
        repo_repo = RepositoryRepo(conn)
        pr_repo = PRRepo(conn)

        unindexed_total = 0
        for repo_cfg in cfg.repositories:
            name = repo_cfg.get("name", "")
            if not name:
                continue
            project_name = cfg.project_name or name.split("/")[0]
            project = project_repo.get_by_name(project_name)
            repository = (
                repo_repo.get(project.id, name) if project is not None else None
            )
            if repository is None:
                typer.echo(f"  {name}: (never ingested)")
                continue

            count = pr_repo.count_for_repository(repository.id)
            unindexed_total += pr_repo.unindexed_count(repository.id)
            op = repo_repo.get_op_state(project.id, name)
            cursor = _fmt_ts(op.cursor_merged_at) if op else "-"
            last_run = (
                f"{op.last_run_status or '-'} @ {_fmt_ts(op.last_run_at)}"
                if op
                else "-"
            )
            typer.echo(f"  {name}: {count} PRs, cursor {cursor}, last run {last_run}")
            if op and op.ingest_errors:
                typer.echo(f"    errored PRs (last run): {len(op.ingest_errors)}")
                for item in op.ingest_errors[:10]:
                    typer.echo(f"      #{item['number']}: {item['error'][:120]}")
                if len(op.ingest_errors) > 10:
                    typer.echo(f"      (+{len(op.ingest_errors) - 10} more)")

        # ---------------- INDEX ----------------
        typer.echo("=== INDEX ===")
        stats = SkillRepo(conn).index_stats()
        typer.echo(f"  vector rows: {stats['total_vectors']} (2 embeddings each)")
        for model, version, n in stats["models"]:
            typer.echo(f"  model: {model} / {version} ({n} rows)")
        typer.echo(f"  unindexed PRs: {unindexed_total}")
        typer.echo(f"  last indexed: {_fmt_ts(stats['last_indexed_at'])}")

    # ---------------- MCP ----------------
    typer.echo("=== MCP ===")
    mcp_status = read_status(cfg.mcp.status_file)
    if mcp_status is None:
        typer.echo("  down (no status file — server not running)")
        return

    updated = datetime.fromisoformat(mcp_status["updated_at"])
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    state = "up" if age <= 3 * HEARTBEAT_SECONDS else f"stale (last seen {int(age)}s ago)"
    typer.echo(f"  {state}, transport {mcp_status['transport']}, pid {mcp_status['pid']}")
    typer.echo(
        f"  requests: {mcp_status['request_count']}, "
        f"p50 {mcp_status['p50_ms'] or '-'} ms, p90 {mcp_status['p90_ms'] or '-'} ms"
    )
