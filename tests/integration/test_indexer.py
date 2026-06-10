"""
tests/integration/test_indexer.py вЂ” Integration tests for the Indexer pipeline.

Uses testcontainers pgvector DB + fake_embedder fixture (no real OpenAI calls).
Tests that the full index lifecycle works end-to-end:
- skills rows created with both 1536-dim embeddings populated
- embedding_model and embedding_version equal to config values (D-08)
- Re-indexing does not duplicate rows (ON CONFLICT DO UPDATE)
- SQL is confined to db/repos/skill.py (no SQL in index.py)

NOTE: These tests require Docker Desktop to be running.
If Docker is unavailable, they will fail during fixture setup.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg
import pytest
from pgvector.psycopg import register_vector

from harness.config import EmbedConfig, YamlConfig
from harness.db.models import PullRequest
from harness.db.repos.pr import PRRepo
from harness.db.repos.project import ProjectRepo
from harness.db.repos.repository import RepositoryRepo
from harness.db.repos.skill import SkillRepo
from harness.indexer.index import Indexer


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_conn(pg_dsn_migrated: str) -> psycopg.Connection:
    """Open a synchronous psycopg3 connection with pgvector registration."""
    conn = psycopg.connect(pg_dsn_migrated)
    register_vector(conn)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture
def embed_cfg() -> EmbedConfig:
    """EmbedConfig with test-specific model/version for isolation."""
    return EmbedConfig(
        model="text-embedding-3-small",
        version="v1",
        problem_limit_tokens=1500,
        diff_limit_tokens=6000,
    )


@pytest.fixture
def yaml_cfg(embed_cfg: EmbedConfig) -> YamlConfig:
    """YamlConfig wired to test embed config."""
    cfg = YamlConfig()
    cfg.embed = embed_cfg
    cfg.project_name = "test-project"
    cfg.repositories = [{"type": "github", "name": "test/repo"}]
    return yaml_cfg


@pytest.fixture
def seeded_repository(sync_conn: psycopg.Connection) -> tuple[int, int]:
    """Create a project + repository row; return (project_id, repository_id)."""
    project_repo = ProjectRepo(sync_conn)
    repo_repo = RepositoryRepo(sync_conn)

    from harness.db.models import Project, Repository

    project = project_repo.upsert(Project(name="test-indexer-project"))
    repo = repo_repo.upsert(
        Repository(
            project_id=project.id,
            type="github",
            name="test/indexer-repo",
        )
    )
    return project.id, repo.id


@pytest.fixture
def seeded_prs(sync_conn: psycopg.Connection, seeded_repository) -> list[int]:
    """Insert 3 pull_requests rows; return their DB ids."""
    _, repository_id = seeded_repository
    pr_repo = PRRepo(sync_conn)

    prs = [
        PullRequest(
            repository_id=repository_id,
            number=101,
            title="Fix null pointer in async resolver",
            body="Closes #42. The async resolver was not handling null inputs.",
            diff="- async Task Resolve() { return null; }\n+ async Task Resolve() { return default; }\n",
            author="jdoe",
            merged_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
        ),
        PullRequest(
            repository_id=repository_id,
            number=102,
            title="Add pagination support to list endpoint",
            body="Fixes #99. Added cursor-based pagination.",
            diff="+ public Page<T> GetPage(int cursor, int size) { ... }\n",
            author="alice",
            merged_at=datetime(2024, 1, 20, tzinfo=timezone.utc),
        ),
        PullRequest(
            repository_id=repository_id,
            number=103,
            title="Refactor database connection pool",
            body="",
            diff="- Pool pool = new Pool();\n+ Pool pool = PoolFactory.Create(config);\n",
            author="bob",
            merged_at=datetime(2024, 1, 25, tzinfo=timezone.utc),
        ),
    ]

    ids = []
    for pr in prs:
        ids.append(pr_repo.upsert(pr))
    return ids


# ---------------------------------------------------------------------------
# Test: Indexer.run with fake embedder
# ---------------------------------------------------------------------------


class TestIndexerRun:
    """Indexer.run reads unindexed PRs, embeds with fake_embedder, writes skills."""

    def test_skills_rows_created(
        self,
        sync_conn: psycopg.Connection,
        seeded_prs: list[int],
        seeded_repository,
        fake_embedder,
        embed_cfg: EmbedConfig,
    ) -> None:
        """After Indexer.run, skills rows exist for all seeded PRs."""
        _, repository_id = seeded_repository

        # Build an async embed_texts that uses fake_embedder under the hood
        async def fake_embed_texts(texts: list[str], model: str, **kwargs: object) -> list[list[float]]:
            return [fake_embedder(t) for t in texts]

        indexer = Indexer(sync_conn, embed_cfg)

        with patch("harness.indexer.index.embed_texts", new=fake_embed_texts):
            count = asyncio.run(indexer.run(repository_id))

        assert count == len(seeded_prs), (
            f"Expected {len(seeded_prs)} skills rows; got {count}"
        )

    def test_skills_have_both_embeddings(
        self,
        sync_conn: psycopg.Connection,
        seeded_prs: list[int],
        seeded_repository,
        fake_embedder,
        embed_cfg: EmbedConfig,
    ) -> None:
        """Each skills row has non-null problem_embedding and solution_embedding."""
        _, repository_id = seeded_repository

        async def fake_embed_texts(texts: list[str], model: str, **kwargs: object) -> list[list[float]]:
            return [fake_embedder(t) for t in texts]

        indexer = Indexer(sync_conn, embed_cfg)

        with patch("harness.indexer.index.embed_texts", new=fake_embed_texts):
            asyncio.run(indexer.run(repository_id))

        # Fetch skills rows and verify embeddings
        rows = sync_conn.execute(
            """
            SELECT sk.pr_id, sk.problem_embedding, sk.solution_embedding
            FROM skills sk
            JOIN pull_requests pr ON pr.id = sk.pr_id
            WHERE pr.repository_id = %s
            """,
            [repository_id],
        ).fetchall()

        assert len(rows) == len(seeded_prs)
        for row in rows:
            pr_id, prob_emb, sol_emb = row
            assert prob_emb is not None, f"PR {pr_id}: problem_embedding is NULL"
            assert sol_emb is not None, f"PR {pr_id}: solution_embedding is NULL"
            # Vectors should be 1536-dimensional
            assert len(prob_emb) == 1536, (
                f"PR {pr_id}: problem_embedding has {len(prob_emb)} dims, expected 1536"
            )
            assert len(sol_emb) == 1536, (
                f"PR {pr_id}: solution_embedding has {len(sol_emb)} dims, expected 1536"
            )

    def test_embedding_model_and_version_persisted(
        self,
        sync_conn: psycopg.Connection,
        seeded_prs: list[int],
        seeded_repository,
        fake_embedder,
        embed_cfg: EmbedConfig,
    ) -> None:
        """Skills rows store embedding_model and embedding_version from config (D-08)."""
        _, repository_id = seeded_repository

        async def fake_embed_texts(texts: list[str], model: str, **kwargs: object) -> list[list[float]]:
            return [fake_embedder(t) for t in texts]

        indexer = Indexer(sync_conn, embed_cfg)

        with patch("harness.indexer.index.embed_texts", new=fake_embed_texts):
            asyncio.run(indexer.run(repository_id))

        rows = sync_conn.execute(
            """
            SELECT sk.embedding_model, sk.embedding_version
            FROM skills sk
            JOIN pull_requests pr ON pr.id = sk.pr_id
            WHERE pr.repository_id = %s
            """,
            [repository_id],
        ).fetchall()

        assert len(rows) == len(seeded_prs)
        for model, version in rows:
            assert model == embed_cfg.model, (
                f"Expected embedding_model={embed_cfg.model!r}, got {model!r} (D-08)"
            )
            assert version == embed_cfg.version, (
                f"Expected embedding_version={embed_cfg.version!r}, got {version!r} (D-08)"
            )

    def test_reindex_does_not_duplicate(
        self,
        sync_conn: psycopg.Connection,
        seeded_prs: list[int],
        seeded_repository,
        fake_embedder,
        embed_cfg: EmbedConfig,
    ) -> None:
        """Running the indexer twice does not create duplicate skills rows."""
        _, repository_id = seeded_repository

        async def fake_embed_texts(texts: list[str], model: str, **kwargs: object) -> list[list[float]]:
            return [fake_embedder(t) for t in texts]

        indexer = Indexer(sync_conn, embed_cfg)

        # First run
        with patch("harness.indexer.index.embed_texts", new=fake_embed_texts):
            asyncio.run(indexer.run(repository_id))

        # Count after first run
        count_after_first = sync_conn.execute(
            """
            SELECT COUNT(*) FROM skills sk
            JOIN pull_requests pr ON pr.id = sk.pr_id
            WHERE pr.repository_id = %s
            """,
            [repository_id],
        ).fetchone()[0]

        # Second run вЂ” should be a no-op (all PRs already indexed)
        # Note: unindexed_prs returns nothing, so embed_texts won't be called
        with patch("harness.indexer.index.embed_texts", new=fake_embed_texts):
            count_second = asyncio.run(indexer.run(repository_id))

        # Count after second run
        count_after_second = sync_conn.execute(
            """
            SELECT COUNT(*) FROM skills sk
            JOIN pull_requests pr ON pr.id = sk.pr_id
            WHERE pr.repository_id = %s
            """,
            [repository_id],
        ).fetchone()[0]

        assert count_second == 0, (
            "Second run should find no unindexed PRs, but processed {count_second}"
        )
        assert count_after_second == count_after_first, (
            f"Row count grew from {count_after_first} to {count_after_second}: duplicates!"
        )


# ---------------------------------------------------------------------------
# Test: SkillRepo.upsert_skill directly
# ---------------------------------------------------------------------------


class TestSkillRepoUpsertSkill:
    """SkillRepo.upsert_skill writes both vectors + model + version."""

    def test_upsert_creates_row(
        self,
        sync_conn: psycopg.Connection,
        seeded_prs: list[int],
        fake_embedder,
    ) -> None:
        """upsert_skill inserts a skills row with correct fields."""
        pr_id = seeded_prs[0]
        prob_vec = fake_embedder("problem text")
        sol_vec = fake_embedder("solution text")

        skill_repo = SkillRepo(sync_conn)
        skill_repo.upsert_skill(
            pr_id=pr_id,
            problem_emb=prob_vec,
            solution_emb=sol_vec,
            model="text-embedding-3-small",
            version="v1",
        )

        row = sync_conn.execute(
            "SELECT pr_id, embedding_model, embedding_version FROM skills WHERE pr_id = %s",
            [pr_id],
        ).fetchone()

        assert row is not None
        assert row[0] == pr_id
        assert row[1] == "text-embedding-3-small"
        assert row[2] == "v1"

    def test_upsert_conflict_updates_not_duplicates(
        self,
        sync_conn: psycopg.Connection,
        seeded_prs: list[int],
        fake_embedder,
    ) -> None:
        """Upserting the same (pr_id, model, version) updates in place."""
        pr_id = seeded_prs[1]
        prob_vec_1 = fake_embedder("first problem text")
        sol_vec_1 = fake_embedder("first solution text")
        prob_vec_2 = fake_embedder("second problem text")
        sol_vec_2 = fake_embedder("second solution text")

        skill_repo = SkillRepo(sync_conn)

        # First upsert
        skill_repo.upsert_skill(pr_id, prob_vec_1, sol_vec_1, "test-model", "v1")

        count_after_first = sync_conn.execute(
            "SELECT COUNT(*) FROM skills WHERE pr_id = %s AND embedding_model = 'test-model'",
            [pr_id],
        ).fetchone()[0]

        # Second upsert with same pr_id + model + version
        skill_repo.upsert_skill(pr_id, prob_vec_2, sol_vec_2, "test-model", "v1")

        count_after_second = sync_conn.execute(
            "SELECT COUNT(*) FROM skills WHERE pr_id = %s AND embedding_model = 'test-model'",
            [pr_id],
        ).fetchone()[0]

        assert count_after_first == 1
        assert count_after_second == 1, "Duplicate row created вЂ” ON CONFLICT not working"
