"""
harness.db.repos.repository — RepositoryRepo data-access layer.

All SQL lives here — never in CLI, Indexer, or Connector (STATE.md constraint).
Uses parameterized queries only (%(name)s placeholders) — never f-string SQL
(V5 input validation / T-01-04 Tampering mitigation).
"""

from __future__ import annotations

import psycopg

from harness.db.models import Repository


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
