"""
harness.db.models — domain dataclasses (pure data carriers, no DB logic).

These dataclasses define the shape of domain objects used across the app.
All DB logic (SQL, pgvector operators) lives exclusively in db/repos/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Project:
    """A top-level project grouping one or more repositories."""

    name: str
    id: int | None = None


@dataclass
class Repository:
    """A source repository (e.g. a GitHub owner/repo) within a project."""

    project_id: int
    type: str  # e.g. "github"
    name: str  # e.g. "owner/repo"
    id: int | None = None


@dataclass
class PullRequest:
    """A merged pull request with its raw content (diff, metadata, files)."""

    repository_id: int
    number: int
    title: str
    body: str | None = None
    diff: str | None = None
    author: str | None = None
    merged_at: datetime | None = None
    linked_issue: str | None = None
    files_changed: list[str] = field(default_factory=list)
    content_hash: str | None = None
    id: int | None = None


@dataclass
class Skill:
    """Embeddings for a pull request (problem + solution), with model metadata."""

    pr_id: int
    embedding_model: str
    embedding_version: str
    problem_embedding: list[float] | None = None
    solution_embedding: list[float] | None = None
    id: int | None = None


@dataclass
class RepoOpState:
    """Operational state for a repository (diagnostic cursor + last-run status).

    Fields mirror the 0002 migration columns added to the repositories table
    (D-A1 / D-B2 / D-B3). All fields are Optional because a repository row
    can exist before any ingest run has been completed.

    cursor_merged_at: DIAGNOSTIC high-water merged_at (GREATEST semantics),
        surfaced by `harness repos`. It does NOT bound traversal or gate fetches —
        resume correctness is owned by the scope re-scan + present-in-DB probe
        (gate #1 / BUG C fix). Reading it as a processing boundary is what caused C.
    cursor_number: PR number at the high-water position (diagnostic tiebreak)
    last_run_at: timestamp of the most recent completed ingest run
    last_run_status: "success" | "error" | None
    last_error: last per-run error message, if any
    """

    cursor_merged_at: datetime | None = None
    cursor_number: int | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    last_error: str | None = None
