"""
senrah.ingester.ingest — Ingester orchestrator.

The Ingester wires the connector (via ConnectorProtocol — structural typing,
NO concrete import of GitHubConnector) to the PRRepo write path.

Forward-pass responsibilities (Plan 03 — INGEST-03/04/05/06):
1. Resolve or create the project + repository rows → repository_id FK.
2. Resolve the scope window lower-bound (resolve_since); for last_n use the
   connector's list_recent_merged_meta pre-pass. Resolve the resume cursor from
   op-state (skipped when --backfill re-applies the scope window).
3. Stream list_merged_prs (diff=None metadata only) over the configured SCOPE
   window — never bounded by a stored cursor (gate #1 / BUG C fix). For each PR:
   - proactive rate-limit throttle (pause when remaining < floor),
   - bot/giant filter on cheap metadata BEFORE any diff fetch (no diff fetched
     for excluded PRs — INGEST-03 structural guarantee),
   - present-in-DB probe (PRRepo.exists): skip PRs already ingested so a scope
     re-scan re-fetches ZERO diffs for them (this is what makes re-scan cheap),
   - fetch the diff for survivors via connector.fetch_diff,
   - upsert in ONE transaction (advance_cursor updates a DIAGNOSTIC high-water
     only — see note below; it is NOT read to bound traversal),
   - per-PR error isolation: log '#N: <err>' to stderr and continue.
4. Record last-run status and emit a per-run filtered-count line to stderr.

Resume model (gate #1 / BUG C): correctness comes from re-scanning the scope
window every run + the present-in-DB probe — NOT from the cursor. A PR missed on
a prior run (interrupted before it, or dropped into per-PR error isolation) is
absent from the DB, so the next run's scope scan re-encounters it and the probe
reports "missing" → it is back-filled. cursor_merged_at is a diagnostic high-water
mark surfaced by `senrah repos`; it bounds nothing.

Boundary constraints (STATE.md):
- Imports connector ONLY as ConnectorProtocol — no concrete GitHubConnector.
- No SQL, no <=> operator, no pgvector usage — all DB access via repos.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import psycopg

from senrah.config import IngestFilterConfig, Scope, resolve_since
from senrah.connectors.base import ConnectorProtocol
from senrah.db.models import Project, PullRequest, Repository
from senrah.db.repos.pr import PRRepo
from senrah.db.repos.project import ProjectRepo
from senrah.db.repos.repository import RepositoryRepo
from senrah.ingester.diff_files import parse_diff_files
from senrah.ingester.filters import is_automation_title, is_bot, is_giant

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
            project_name: Project name from senrah.yaml.
            repo_type: Repository type string (default "github").
            last_n: Legacy count bound used when ``scope`` is None.
            scope: Per-repo / default ingest scope (D-A3). When mode == "last_n"
                the window lower-bound is resolved from a connector pre-pass.
            backfill: Inert for traversal (retained for CLI compatibility). Every
                run already re-scans the configured scope window; use scope "all"
                for a deep re-enumeration. (Historically: ignore the stored cursor.)
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

        # ---- Resolve the scope-window lower bound from config (Pattern 3) ----
        # The stored cursor is NOT read to bound traversal (gate #1 / BUG C): every
        # run re-scans the configured scope window. The `backfill` flag is now inert
        # for traversal — re-scan is unconditional — and is retained only for CLI
        # compatibility (use scope "all" for a deep re-enumeration).
        _ = backfill
        since, effective_last_n = self._resolve_window(
            connector, repo_full_name, scope, last_n
        )

        # ---- Forward pass ----
        upserted = 0
        filtered_bot = 0
        filtered_title = 0
        filtered_giant = 0
        filtered_empty = 0
        skipped_present = 0  # already in DB (probe) — re-scan cost avoided
        status = "success"
        last_error: str | None = None
        pr_errors: list[dict] = []  # per-PR failures, persisted for `status` (OPS-04)
        rate_status = None

        try:
            stream = connector.list_merged_prs(
                repo_full_name,
                since=since,
                last_n=effective_last_n,
            )
            for index, raw_pr in enumerate(stream):
                # (1) Proactive throttle — refresh status every K PRs.
                if index % _RATE_CHECK_INTERVAL == 0:
                    rate_status = connector.rate_limit_status()
                if rate_status is not None and rate_status.remaining < filters.rate_limit_floor:
                    self._throttle(rate_status)
                    rate_status = connector.rate_limit_status()  # refresh after wait

                # (2) Bot filter — author is a list-payload field, free (no GET).
                if is_bot(raw_pr.author, filters.stop_list):
                    filtered_bot += 1
                    continue

                # (2b) Automation-title filter — title is also list-payload,
                # free. Catches recurring automation whose author is not
                # bot-suffixed (configured via ingest.title_stop_patterns).
                if is_automation_title(raw_pr.title, filters.title_stop_patterns):
                    filtered_title += 1
                    continue

                # (3) Present-in-DB probe (gate #1 / BUG C) — BEFORE size().
                # Skip PRs already in pull_requests via a cheap DB read, so a scope
                # re-scan pays NEITHER the diff fetch NOR the per-PR completion GET
                # for an already-ingested PR. Re-running the giant filter on a PR
                # already accepted into the DB is meaningless, and paying its
                # completion GET would re-introduce the Finding-2 N+1 on every
                # re-scan. Criterion is strictly "present in DB" — never a cursor
                # compare — so a PR missed on a prior run (interrupt or per-PR
                # error isolation) is absent here and gets re-fetched.
                if pr_repo.exists(repository_id, raw_pr.number):
                    skipped_present += 1
                    continue

                # (4) size() fires the per-PR completion GET for the giant check —
                # now paid ONLY for PRs not already in the DB (bots and present PRs
                # are already out, so neither costs a completion GET — Finding 2).
                changed_files, additions, deletions = raw_pr.size()
                if is_giant(
                    changed_files,
                    additions,
                    deletions,
                    max_files=filters.max_files,
                    max_lines=filters.max_lines,
                ):
                    filtered_giant += 1
                    continue

                # (5) Survivor: fetch the diff (the ONLY diff-fetch call site).
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
                        # Design B traversal yields files_changed == [] (no
                        # get_files() call); derive the list from the diff we
                        # already fetched — zero extra API cost.
                        files_changed=parse_diff_files(diff) or raw_pr.files_changed,
                    )
                    # (6) Atomic upsert. advance_cursor here updates a DIAGNOSTIC
                    # high-water mark only (surfaced by `senrah repos`); it does
                    # NOT bound traversal or gate fetches — resume correctness is
                    # owned by the scope re-scan + the present-in-DB probe above.
                    with self._conn.transaction():
                        pr_repo.upsert(pr)
                        repository_repo.advance_cursor(
                            repository_id,
                            merged_at=raw_pr.merged_at,
                            number=raw_pr.number,
                        )
                    upserted += 1
                except Exception as exc:
                    # Per-PR isolation: the transaction rolled back; the PR stays
                    # absent from the DB, so the next scope re-scan back-fills it
                    # via the probe. Log and continue (INGEST-05).
                    print(f"[ingester] #{raw_pr.number}: {exc}", file=sys.stderr)
                    pr_errors.append({"number": raw_pr.number, "error": str(exc)})

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
                ingest_errors=pr_errors,
            )
            print(
                f"[ingester] {repo_full_name}: {upserted} upserted, "
                f"{skipped_present} already-present, "
                f"filtered {filtered_bot} bot / {filtered_title} automation-title / "
                f"{filtered_giant} giant / {filtered_empty} empty-diff",
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
