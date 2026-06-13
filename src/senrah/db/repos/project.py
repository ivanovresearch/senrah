"""
senrah.db.repos.project — ProjectRepo data-access layer.

All SQL lives here — never in CLI, Indexer, or Connector (STATE.md constraint).
Uses parameterized queries only (%(name)s placeholders) — never f-string SQL
(V5 input validation / T-01-04 Tampering mitigation).
"""

from __future__ import annotations

import psycopg

from senrah.db.models import Project


class ProjectRepo:
    """Data-access object for the projects table.

    Provides upsert + lookup by name.  The ingest pipeline uses this to
    resolve the configured project name to a DB primary key.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, project: Project) -> Project:
        """Insert or update a project by name.

        Returns the project with its DB-assigned id populated.
        Uses INSERT ... ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
        so that a re-run of `senrah ingest` is idempotent.
        """
        row = self._conn.execute(
            """
            INSERT INTO projects (name)
            VALUES (%(name)s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            {"name": project.name},
        ).fetchone()
        return Project(id=row[0], name=project.name)

    def get_by_name(self, name: str) -> Project | None:
        """Return the Project with the given name, or None if not found."""
        row = self._conn.execute(
            "SELECT id, name FROM projects WHERE name = %(name)s",
            {"name": name},
        ).fetchone()
        if row is None:
            return None
        return Project(id=row[0], name=row[1])
