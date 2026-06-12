"""Add ingest_errors column to repositories table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-12

OPS-04 (`harness status` ingest section): the spec requires a LIST of
errored PRs, but per-PR errors were only logged to stderr and lost.
ingest_errors stores the most recent run's per-PR failures as JSONB:
[{"number": 38123, "error": "..."}]. Overwritten on every run (empty
array on a clean run) — it is a per-run diagnostic, not an accumulating
history.
"""

from __future__ import annotations

from alembic import op

# revision identifiers
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE repositories
            ADD COLUMN ingest_errors JSONB NOT NULL DEFAULT '[]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE repositories
            DROP COLUMN IF EXISTS ingest_errors
        """
    )
