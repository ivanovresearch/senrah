"""
harness.db.repos.pr — PRRepo data-access layer.

All SQL lives here — never in CLI, Ingester, or Connector (STATE.md constraint).
Uses parameterized queries only (%(name)s placeholders) — never f-string SQL
(V5 input validation / T-02-02 Tampering mitigation: untrusted PR content
stored as data, never executed).

Provides:
- PRRepo.upsert(pr) → int (row id)
  INSERT ... ON CONFLICT (repository_id, number) DO UPDATE
  Persists diff, files_changed (JSONB), and content_hash (hash of title+body+diff).
- PRRepo.unindexed_prs(repository_id) → list[PullRequest]
  Returns PRs lacking a skills row (consumed by Plan 01-03 Indexer).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg

from harness.db.models import PullRequest


class PRRepo:
    """Data-access object for the pull_requests table.

    Provides upsert for ingest and unindexed_prs for the indexer.
    All SQL is parameterized — no f-string interpolation (T-02-02).
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # upsert — write path for Ingester
    # ------------------------------------------------------------------

    def upsert(self, pr: PullRequest) -> int:
        """Insert or update a pull request.

        Conflict key: (repository_id, number) — a PR number is unique within
        a repository.  On conflict, all mutable fields are updated so that a
        re-run of `harness ingest` refreshes stale rows.

        files_changed is stored as JSONB (psycopg3 serializes list[str] to JSON
        natively via Jsonb wrapper — Open Question 1 resolution from SUMMARY.md).

        content_hash is SHA-256(title + body + diff) and enables future change
        detection without re-fetching the diff.

        Returns:
            The DB-assigned row id (useful for linking skills rows later).
        """
        content_hash = _compute_content_hash(
            pr.title, pr.body or "", pr.diff or ""
        )

        row = self._conn.execute(
            """
            INSERT INTO pull_requests (
                repository_id,
                number,
                title,
                body,
                diff,
                author,
                merged_at,
                linked_issue,
                files_changed,
                content_hash
            )
            VALUES (
                %(repository_id)s,
                %(number)s,
                %(title)s,
                %(body)s,
                %(diff)s,
                %(author)s,
                %(merged_at)s,
                %(linked_issue)s,
                %(files_changed)s::jsonb,
                %(content_hash)s
            )
            ON CONFLICT (repository_id, number) DO UPDATE SET
                title         = EXCLUDED.title,
                body          = EXCLUDED.body,
                diff          = EXCLUDED.diff,
                author        = EXCLUDED.author,
                merged_at     = EXCLUDED.merged_at,
                linked_issue  = EXCLUDED.linked_issue,
                files_changed = EXCLUDED.files_changed,
                content_hash  = EXCLUDED.content_hash
            RETURNING id
            """,
            {
                "repository_id": pr.repository_id,
                "number": pr.number,
                "title": pr.title,
                "body": pr.body,
                "diff": pr.diff,
                "author": pr.author,
                "merged_at": pr.merged_at,
                "linked_issue": pr.linked_issue,
                "files_changed": json.dumps(pr.files_changed),
                "content_hash": content_hash,
            },
        ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # exists — present-in-DB probe for the Ingester (gate #1 / BUG C fix)
    # ------------------------------------------------------------------

    def exists(self, repository_id: int, number: int) -> bool:
        """Return True if (repository_id, number) is already in pull_requests.

        The Ingester calls this immediately before fetch_diff so an already-
        ingested PR costs ZERO diff fetch on a scope re-scan. The criterion is
        strictly "present in the DB" — NOT a comparison against any cursor. That
        is what makes resume correct AND recovers errored PRs for free: a PR that
        was skipped on a prior run (interrupt or per-PR error isolation) is absent
        here, so the probe reports "missing" and it gets re-fetched (gate #1).

        Cheap: a covered lookup on the (repository_id, number) unique index.
        """
        row = self._conn.execute(
            """
            SELECT 1
            FROM pull_requests
            WHERE repository_id = %(repository_id)s
              AND number = %(number)s
            """,
            {"repository_id": repository_id, "number": number},
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # unindexed_prs — read path for Indexer (Plan 01-03)
    # ------------------------------------------------------------------

    def unindexed_prs(self, repository_id: int) -> list[PullRequest]:
        """Return PRs that have no corresponding skills row.

        A PR is "unindexed" when there is no skills row referencing it.
        The Indexer uses this to find work to do without re-embedding already
        indexed PRs.

        Args:
            repository_id: Filter to a single repository.

        Returns:
            List of PullRequest dataclasses with all fields populated.
        """
        rows = self._conn.execute(
            """
            SELECT
                pr.id,
                pr.repository_id,
                pr.number,
                pr.title,
                pr.body,
                pr.diff,
                pr.author,
                pr.merged_at,
                pr.linked_issue,
                pr.files_changed,
                pr.content_hash
            FROM pull_requests pr
            LEFT JOIN skills sk ON sk.pr_id = pr.id
            WHERE pr.repository_id = %(repository_id)s
              AND sk.id IS NULL
            ORDER BY pr.merged_at DESC
            """,
            {"repository_id": repository_id},
        ).fetchall()

        return [
            PullRequest(
                id=row[0],
                repository_id=row[1],
                number=row[2],
                title=row[3],
                body=row[4],
                diff=row[5],
                author=row[6],
                merged_at=row[7],
                linked_issue=row[8],
                files_changed=row[9] if row[9] is not None else [],
                content_hash=row[10],
            )
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_content_hash(title: str, body: str, diff: str) -> str:
    """Return SHA-256 hex digest of title + body + diff.

    Enables future change detection: if the hash is unchanged on a re-ingest,
    the skills row may be reused without re-embedding.
    """
    combined = f"{title}\n{body}\n{diff}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
