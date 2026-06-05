"""
harness.db.repos.repository — RepositoryRepo data-access layer.

All SQL lives here — never in CLI, Indexer, or Connector (STATE.md constraint).
Uses parameterized queries only (%(name)s placeholders) — never f-string SQL
(V5 input validation / T-01-04 Tampering mitigation).
"""

from __future__ import annotations

import psycopg

from harness.db.models import Repository, RepoOpState


class RepositoryRepo:
    """Data-access object for the repositories table.

    Provides upsert + lookup by project_id + name.  The ingest pipeline uses
    this to resolve the configured repo name to a DB primary key.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, repository: Repository) -> Repository:
        """Insert or update a repository (identified by project_id + name).

        Returns the repository with its DB-assigned id populated.
        Uses INSERT ... ON CONFLICT DO UPDATE for idempotency.
        """
        row = self._conn.execute(
            """
            INSERT INTO repositories (project_id, type, name)
            VALUES (%(project_id)s, %(type)s, %(name)s)
            ON CONFLICT (project_id, name)
                DO UPDATE SET type = EXCLUDED.type
            RETURNING id
            """,
            {
                "project_id": repository.project_id,
                "type": repository.type,
                "name": repository.name,
            },
        ).fetchone()
        return Repository(
            id=row[0],
            project_id=repository.project_id,
            type=repository.type,
            name=repository.name,
        )

    def get(self, project_id: int, name: str) -> Repository | None:
        """Return the Repository with the given project_id and name, or None."""
        row = self._conn.execute(
            """
            SELECT id, project_id, type, name
            FROM repositories
            WHERE project_id = %(project_id)s
              AND name = %(name)s
            """,
            {"project_id": project_id, "name": name},
        ).fetchone()
        if row is None:
            return None
        return Repository(id=row[0], project_id=row[1], type=row[2], name=row[3])

    # ---------------------------------------------------------------------------
    # Op-state methods (cursor + last-run) — execute-only, never committing.
    # The Ingester owns the transaction (D-B3); these methods only execute SQL.
    # All parameterized %(name)s placeholders — no f-string SQL (T-03-02).
    # ---------------------------------------------------------------------------

    def get_op_state(self, project_id: int, name: str) -> "RepoOpState | None":
        """Return the op-state for the repository identified by project_id + name.

        Returns None if no row exists (repository not yet registered or never run).
        """
        row = self._conn.execute(
            """
            SELECT cursor_merged_at, cursor_number, last_run_at, last_run_status, last_error
            FROM repositories
            WHERE project_id = %(project_id)s AND name = %(name)s
            """,
            {"project_id": project_id, "name": name},
        ).fetchone()
        if row is None:
            return None
        return RepoOpState(
            cursor_merged_at=row[0],
            cursor_number=row[1],
            last_run_at=row[2],
            last_run_status=row[3],
            last_error=row[4],
        )

    def advance_cursor(
        self,
        repository_id: int,
        merged_at: object,
        number: int,
    ) -> None:
        """Advance the repository's high-water cursor atomically (GREATEST semantics).

        Uses GREATEST(cursor_merged_at, %(merged_at)s) so that an out-of-created-order
        older merge cannot move the cursor backward (D-B3 / T-03-04 / Pattern 4).

        MUST be called inside the Ingester's per-PR transaction block. Never calls
        conn.commit() — the Ingester owns commit/rollback.
        """
        self._conn.execute(
            """
            UPDATE repositories
               SET cursor_merged_at = GREATEST(cursor_merged_at, %(merged_at)s),
                   cursor_number    = %(number)s
             WHERE id = %(repository_id)s
            """,
            {
                "repository_id": repository_id,
                "merged_at": merged_at,
                "number": number,
            },
        )

    def set_last_run(
        self,
        repository_id: int,
        status: str,
        ran_at: object,
        last_error: str | None,
    ) -> None:
        """Record the outcome of the most recent ingest run.

        Called in the Ingester's finally: block regardless of success/failure.
        Never calls conn.commit() — the Ingester owns transaction lifecycle.
        """
        self._conn.execute(
            """
            UPDATE repositories
               SET last_run_at     = %(ran_at)s,
                   last_run_status = %(status)s,
                   last_error      = %(last_error)s
             WHERE id = %(repository_id)s
            """,
            {
                "repository_id": repository_id,
                "ran_at": ran_at,
                "status": status,
                "last_error": last_error,
            },
        )
