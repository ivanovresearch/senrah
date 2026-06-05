"""Add op-state columns to repositories table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-05

Implements D-B2 single high-water mark per repository:
- cursor_merged_at: high-water merged_at (GREATEST semantics, Pattern 4)
- cursor_number: PR number tiebreak at the cursor position
- last_run_at: timestamp of the most recent completed ingest run
- last_run_status: "success" | "error"
- last_error: last per-run error message

Existing rows get NULL cursor → "never run" → first ingest run uses the scope
window to determine the starting point (no data migration required).
"""

from __future__ import annotations

from alembic import op

# revision identifiers
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE repositories
            ADD COLUMN cursor_merged_at TIMESTAMPTZ,
            ADD COLUMN cursor_number    INTEGER,
            ADD COLUMN last_run_at      TIMESTAMPTZ,
            ADD COLUMN last_run_status  TEXT,
            ADD COLUMN last_error       TEXT
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE repositories
            DROP COLUMN IF EXISTS cursor_merged_at,
            DROP COLUMN IF EXISTS cursor_number,
            DROP COLUMN IF EXISTS last_run_at,
            DROP COLUMN IF EXISTS last_run_status,
            DROP COLUMN IF EXISTS last_error
        """
    )
