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
