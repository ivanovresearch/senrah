"""
harness.ingester.ingest — Ingester orchestrator.

The Ingester wires the connector (via ConnectorProtocol — structural typing,
NO concrete import of GitHubConnector) to the PRRepo write path.

Responsibilities:
1. Resolve or create the project + repository rows (via ProjectRepo / RepositoryRepo)
   to get the repository_id FK.
2. Stream list_merged_prs(repo_full_name, last_n) from the connector.
3. Upsert each RawPR into pull_requests via PRRepo.upsert.
4. Per-PR error isolation: a single bad PR logs to stderr and continues
   (T-02-04 DoS mitigation; full backoff deferred to Phase 3 / INGEST-06).

Boundary constraints (STATE.md):
- Imports connector ONLY as ConnectorProtocol (the Protocol type) — no concrete
  import of GitHubConnector.  The composition root (cli/ingest.py) is the only
  place the concrete connector is instantiated.
- No SQL, no <=> operator, no pgvector usage — all DB access via PRRepo.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import psycopg

from harness.connectors.base import ConnectorProtocol
from harness.db.models import Project, PullRequest, Repository
from harness.db.repos.pr import PRRepo
from harness.db.repos.project import ProjectRepo
from harness.db.repos.repository import RepositoryRepo


class Ingester:
    """Orchestrates the ingest pipeline: connector → pull_requests table.

    Usage (at the CLI composition root):
        connector = GitHubConnector(env.github_token)  # concrete, here only
        ingester = Ingester(conn)
        ingester.run(connector, "dotnet/runtime", last_n=100)

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
    ) -> int:
        """Ingest merged PRs from the connector into the database.

        Resolves or creates project + repository rows, then streams PRs from
        the connector and upserts each one.  Per-PR errors are logged to stderr
        and do not abort the run (T-02-04 basic isolation).

        Args:
            connector: Any object implementing ConnectorProtocol.
            repo_full_name: "owner/repo" string (D-05 addressing).
            project_name: Project name from harness.yaml.
            repo_type: Repository type string (default "github").
            last_n: Number of merged PRs to fetch.  None = full history.
                    Default 100 per D-04 (overridden by CLI --last-n / --all).

        Returns:
            Number of PRs successfully upserted.
        """
        # Resolve / create project + repository rows
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

        upserted = 0
        for raw_pr in connector.list_merged_prs(repo_full_name, last_n=last_n):
            try:
                pr = PullRequest(
                    repository_id=repository_id,
                    number=raw_pr.number,
                    title=raw_pr.title,
                    body=raw_pr.body,
                    diff=raw_pr.diff,
                    author=raw_pr.author,
                    merged_at=raw_pr.merged_at,
                    linked_issue=raw_pr.linked_issue,
                    files_changed=raw_pr.files_changed,
                )
                pr_repo.upsert(pr)
                upserted += 1
            except Exception as exc:
                # Per-PR isolation: log and continue (T-02-04)
                # Full backoff strategy deferred to Phase 3 (INGEST-06)
                print(
                    f"[ingester] ERROR on PR #{raw_pr.number}: {exc}",
                    file=sys.stderr,
                )

        return upserted
