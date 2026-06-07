"""
harness.ingester.ingest — Ingester orchestrator.

The Ingester wires the connector (via ConnectorProtocol — structural typing,
NO concrete import of GitHubConnector) to the PRRepo write path.

Forward-pass responsibilities (Plan 03 — INGEST-03/04/05/06):
1. Resolve or create the project + repository rows → repository_id FK.
2. Resolve the scope window lower-bound (resolve_since); for last_n use the
   connector's list_recent_merged_meta pre-pass. Resolve the resume cursor from
   op-state (skipped when --backfill re-applies the scope window).
3. Stream list_merged_prs (diff=None metadata only). For each PR:
   - proactive rate-limit throttle (pause when remaining < floor),
   - bot/giant filter on cheap metadata BEFORE any diff fetch (no diff fetched
     for excluded PRs — INGEST-03 structural guarantee),
   - fetch the diff for survivors via connector.fetch_diff,
   - upsert + advance_cursor in ONE transaction (D-B3 atomicity),
   - per-PR error isolation: log '#N: <err>' to stderr and continue.
4. Record last-run status and emit a per-run filtered-count line to stderr.

Boundary constraints (STATE.md):
- Imports connector ONLY as ConnectorProtocol — no concrete GitHubConnector.
- No SQL, no <=> operator, no pgvector usage — all DB access via repos.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg

from harness.config import IngestFilterConfig, Scope, resolve_since
from harness.connectors.base import ConnectorProtocol, PRCursor
from harness.db.models import Project, PullRequest, Repository
from harness.db.repos.pr import PRRepo
from harness.db.repos.project import ProjectRepo
from harness.db.repos.repository import RepositoryRepo
from harness.ingester.filters import is_bot, is_giant

# Refresh the rate-limit status every N PRs rather than every PR (the status
# call is itself an API request — RESEARCH Pattern 5 anti-pattern note).
_RATE_CHECK_INTERVAL = 50


class Ingester:
    """Orchestrates the ingest pipeline: connector → pull_requests table.

    Usage (at the CLI composition root):
        connector = GitHubConnector(env.github_token)  # concrete, here only
        ingester = Ingester(conn)
        ingester.run(connector, "dotnet/runtime", "proj", scope=Scope("last_n", 200))

    The Ingester never imports GitHubConnector — it accepts any object that
    structurally satisfies ConnectorProtocol.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def run(
        self,
        connector: ConnectorProtocol,
        repo_full_name: str,
        project_name: str,
        repo_type: str = "github",
        last_n: int | None = 100,
        *,
        scope: Scope | None = None,
        backfill: bool = False,
        filters: IngestFilterConfig | None = None,
    ) -> int:
        """Ingest merged PRs from the connector into the database.

        Args:
            connector: Any object implementing ConnectorProtocol.
            repo_full_name: "owner/repo" string (D-05 addressing).
            project_name: Project name from harness.yaml.
            repo_type: Repository type string (default "github").
            last_n: Legacy count bound used when ``scope`` is None.
            scope: Per-repo / default ingest scope (D-A3). When mode == "last_n"
                the window lower-bound is resolved from a connector pre-pass.
            backfill: Re-apply the current scope window, ignoring the stored
                cursor (D-B2 forward-only deepen).
            filters: Bot/giant/throttle knobs (defaults applied when None).

        Returns:
            Number of PRs successfully upserted.
        """
        filters = filters or IngestFilterConfig()

        project_repo = ProjectRepo(self._conn)
        repository_repo = RepositoryRepo(self._conn)
        pr_repo = PRRepo(self._conn)

        project = project_repo.upsert(Project(name=project_name))
        repository = repository_repo.upsert(
            Repository(
                project_id=project.id,  # type: ignore[arg-type]
                type=repo_type,
                name=repo_full_name,
            )
        )
        repository_id: int = repository.id  # type: ignore[assignment]

        # ---- Resolve resume cursor from op-state (skipped under --backfill) ----
        op_state = repository_repo.get_op_state(project.id, repo_full_name)  # type: ignore[arg-type]
        cursor: PRCursor | None = None
        if (
            not backfill
            and op_state is not None
            and op_state.cursor_merged_at is not None
        ):
            cursor = PRCursor(
                merged_at=op_state.cursor_merged_at,
                number=op_state.cursor_number or 0,
            )

        # ---- Resolve the scope-window lower bound (Pattern 3) ----
        since, effective_last_n = self._resolve_window(
            connector, repo_full_name, scope, last_n
        )

        # Incremental runs apply the overlap re-yield window (Design B, Plan 02);
        # the connector consumes it. Derivation is the tunable floor for now.
        overlap_margin: timedelta | None = None
        if cursor is not None:
            overlap_margin = timedelta(seconds=filters.overlap_margin_seconds)

        # ---- Forward pass ----
        upserted = 0
        filtered_bot = 0
        filtered_giant = 0
        filtered_empty = 0
        status = "success"
        last_error: str | None = None
        rate_status = None

        try:
            stream = connector.list_merged_prs(
                repo_full_name,
                since=since,
                cursor=cursor,
                last_n=effective_last_n,
                overlap_margin=overlap_margin,
            )
            for index, raw_pr in enumerate(stream):
                # (1) Proactive throttle — refresh status every K PRs.
                if index % _RATE_CHECK_INTERVAL == 0:
                    rate_status = connector.rate_limit_status()
                if rate_status is not None and rate_status.remaining < filters.rate_limit_floor:
                    self._throttle(rate_status)
                    rate_status = connector.rate_limit_status()  # refresh after wait

                # (2) Pre-fetch filter on cheap metadata (NO diff fetched yet).
                if is_bot(raw_pr.author, filters.stop_list):
                    filtered_bot += 1
                    continue
                if is_giant(
                    raw_pr.changed_files,
                    raw_pr.additions,
                    raw_pr.deletions,
                    max_files=filters.max_files,
                    max_lines=filters.max_lines,
                ):
                    filtered_giant += 1
                    continue

                # (3) Survivor: fetch the diff (the ONLY diff-fetch call site).
                try:
                    diff = connector.fetch_diff(repo_full_name, raw_pr.number)
                    if not diff:
                        filtered_empty += 1
                        continue
                    pr = PullRequest(
                        repository_id=repository_id,
                        number=raw_pr.number,
                        title=raw_pr.title,
                        body=raw_pr.body,
                        diff=diff,
                        author=raw_pr.author,
                        merged_at=raw_pr.merged_at,
                        linked_issue=raw_pr.linked_issue,
                        files_changed=raw_pr.files_changed,
                    )
                    # (4) Atomic: upsert + advance_cursor in one transaction (D-B3).
                    with self._conn.transaction():
                        pr_repo.upsert(pr)
                        repository_repo.advance_cursor(
                            repository_id,
                            merged_at=raw_pr.merged_at,
                            number=raw_pr.number,
                        )
                    upserted += 1
                except Exception as exc:
                    # Per-PR isolation: the transaction rolled back (cursor intact);
                    # log and continue (INGEST-05).
                    print(f"[ingester] #{raw_pr.number}: {exc}", file=sys.stderr)

                if filters.inter_fetch_delay > 0:
                    time.sleep(filters.inter_fetch_delay)
        except Exception as exc:  # run-level failure (e.g. traversal/auth)
            status = "error"
            last_error = str(exc)
            raise
        finally:
            repository_repo.set_last_run(
                repository_id,
                status=status,
                ran_at=datetime.now(timezone.utc),
                last_error=last_error,
            )
            print(
                f"[ingester] {repo_full_name}: {upserted} upserted, "
                f"filtered {filtered_bot} bot / {filtered_giant} giant / "
                f"{filtered_empty} empty-diff",
                file=sys.stderr,
            )

        return upserted

    def _resolve_window(
        self,
        connector: ConnectorProtocol,
        repo_full_name: str,
        scope: Scope | None,
        last_n: int | None,
    ) -> tuple[datetime | None, int | None]:
        """Resolve (since lower-bound, effective last_n) from the scope.

        For mode == "last_n" the window lower-bound is the merged_at of the
        oldest of the newest N merged PRs — obtained via a connector metadata
        pre-pass (list_recent_merged_meta), then min'd by resolve_since.
        """
        if scope is None:
            return None, last_n

        if scope.mode == "last_n":
            n = int(scope.value)  # type: ignore[arg-type]
            recent = connector.list_recent_merged_meta(repo_full_name, n)
            since = resolve_since(
                scope,
                now=datetime.now(timezone.utc),
                last_n_merged_at_provider=[m.merged_at for m in recent],
            )
            return since, n

        since = resolve_since(scope, now=datetime.now(timezone.utc))
        return since, None

    @staticmethod
    def _throttle(rate_status) -> None:  # type: ignore[no-untyped-def]
        """Pause until the rate-limit reset (cursor already committed per-PR)."""
        wait_s = max(0.0, (rate_status.reset_at - datetime.now(timezone.utc)).total_seconds())
        print(
            f"[ingester] rate limit low ({rate_status.remaining} remaining); "
            f"pausing {wait_s:.0f}s until reset",
            file=sys.stderr,
        )
        time.sleep(wait_s)
