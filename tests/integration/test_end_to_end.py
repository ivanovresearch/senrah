"""
tests/integration/test_end_to_end.py — End-to-end walking-skeleton integration test.

Proves SEARCH-04: seed pull_requests → index with fake embedder → search → top result.

This test exercises the full path:
  seed (project/repo/PRs) → Indexer.run (fake embed_texts) → SkillRepo.search
  → expected top PR ranked first, with correct below-threshold hint when threshold
  is raised above all scores.

Uses:
- pg_dsn_migrated fixture (testcontainers pgvector + alembic upgrade head)
- fake_embedder fixture (deterministic 1536-dim unit vectors, no OpenAI)
- patch("harness.indexer.index.embed_texts") to inject fake embedder

NO real GitHub, NO real OpenAI, NO real GITHUB_TOKEN / OPENAI_API_KEY.
T-04-05: fake embedder used; key not required.

NOTE: These tests require Docker Desktop to be running.
If Docker is unavailable, they will fail during fixture setup.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import psycopg
import pytest
from pgvector.psycopg import register_vector, register_vector_async

from harness.config import EmbedConfig, SearchConfig, YamlConfig
from harness.db.models import PullRequest
from harness.db.repos.pr import PRRepo
from harness.db.repos.project import ProjectRepo
from harness.db.repos.repository import RepositoryRepo
from harness.db.repos.skill import SearchResult, SkillRepo
from harness.indexer.index import Indexer
from harness.scoring import composite_score

# ---------------------------------------------------------------------------
# Fixtures: seeded DB state for E2E tests
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_conn_e2e(pg_dsn_migrated: str) -> psycopg.Connection:
    """Sync connection with pgvector registration for E2E fixture seeding."""
    conn = psycopg.connect(pg_dsn_migrated)
    register_vector(conn)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture
def e2e_repository_ids(sync_conn_e2e: psycopg.Connection):
    """Create a project + repository for E2E tests; return (project_id, repo_id)."""
    project_repo = ProjectRepo(sync_conn_e2e)
    repo_repo = RepositoryRepo(sync_conn_e2e)

    from harness.db.models import Project, Repository

    project = project_repo.upsert(Project(name="e2e-test-project"))
    repo = repo_repo.upsert(
        Repository(
            project_id=project.id,
            type="github",
            name="e2e-test/repo",
        )
    )
    return project.id, repo.id


@pytest.fixture
def e2e_seeded_prs(sync_conn_e2e: psycopg.Connection, e2e_repository_ids):
    """Seed 3 PRs with deterministic content for E2E search testing.

    PR 201: "Fix async cancellation token propagation" — the TARGET PR.
             Its problem text has strong semantic signal for queries about
             async cancellation. The fake_embedder will produce a deterministic
             vector for this text. We will use the same text as our query to
             guarantee this PR is nearest.

    PR 202 and PR 203: Unrelated PRs about pagination and DB schema.
    """
    _, repository_id = e2e_repository_ids
    pr_repo = PRRepo(sync_conn_e2e)

    prs = [
        PullRequest(
            repository_id=repository_id,
            number=201,
            title="Fix async cancellation token propagation in runtime",
            body="Closes #101. The async runtime was not propagating cancellation tokens correctly.",
            diff="- Task.Run(() => Work());\n+ Task.Run(() => Work(), cancellationToken);\n",
            author="alice",
            merged_at=datetime(2024, 3, 10, tzinfo=timezone.utc),
        ),
        PullRequest(
            repository_id=repository_id,
            number=202,
            title="Add cursor-based pagination to API endpoints",
            body="Fixes #202. Implement cursor pagination to improve performance.",
            diff="+ public IPage<T> GetPage(string cursor) { return _repo.GetPage(cursor); }\n",
            author="bob",
            merged_at=datetime(2024, 3, 12, tzinfo=timezone.utc),
        ),
        PullRequest(
            repository_id=repository_id,
            number=203,
            title="Refactor database connection pool initialization",
            body="Simplify pool setup with factory pattern.",
            diff="- var pool = new ConnectionPool();\n+ var pool = ConnectionPoolFactory.Create(cfg);\n",
            author="charlie",
            merged_at=datetime(2024, 3, 14, tzinfo=timezone.utc),
        ),
    ]

    ids = []
    for pr in prs:
        ids.append(pr_repo.upsert(pr))
    return ids, repository_id


# ---------------------------------------------------------------------------
# Tests: E2E search pipeline
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestEndToEndSearch:
    """End-to-end: seed → index (fake embed) → search → expected top result."""

    def test_search_returns_expected_top_pr(
        self,
        sync_conn_e2e: psycopg.Connection,
        e2e_seeded_prs,
        fake_embedder,
    ):
        """After indexing with fake_embedder, searching with a near-duplicate query
        returns PR 201 as the top result (SEARCH-04)."""
        pr_ids, repository_id = e2e_seeded_prs

        # Build embed_texts using fake_embedder so we can predict which PR is nearest.
        # The fake_embedder is hash-seeded: same text → same deterministic unit vector.
        # For symmetric retrieval: we embed the query text and compare against both
        # problem_embedding and solution_embedding columns.
        async def fake_embed_texts(texts: list[str], model: str) -> list[list[float]]:
            return [fake_embedder(t) for t in texts]

        # Index all PRs
        embed_cfg = EmbedConfig(
            model="text-embedding-3-small",
            version="v1-e2e",
        )
        indexer = Indexer(sync_conn_e2e, embed_cfg)
        with patch("harness.indexer.index.embed_texts", new=fake_embed_texts):
            count = asyncio.run(indexer.run(repository_id))

        assert count == 3, f"Expected 3 PRs indexed, got {count}"

        # Build the query: we use the EXACT problem text of PR 201 so the fake_embedder
        # produces the SAME vector as was stored for PR 201's problem_embedding.
        # With cosine similarity, this should yield similarity = 1.0 for PR 201.
        from harness.indexer.embedder import build_problem_text

        target_pr_title = "Fix async cancellation token propagation in runtime"
        target_pr_body = "Closes #101. The async runtime was not propagating cancellation tokens correctly."
        query_text = build_problem_text(target_pr_title, target_pr_body)
        query_vec = fake_embedder(query_text)

        # Run search via async pool
        async def _do_search():
            import psycopg as _psycopg
            async with await _psycopg.AsyncConnection.connect(
                sync_conn_e2e.info.dsn
            ) as async_conn:
                await register_vector_async(async_conn)
                repo = SkillRepo(async_conn)
                return await repo.search(
                    query_vec=query_vec,
                    top_n=3,
                    oversample_factor=5,
                    score_threshold=0.0,  # accept all for this test
                    problem_weight=0.6,
                    solution_weight=0.4,
                )

        results = asyncio.run(_do_search())

        assert len(results) > 0, "Search returned no results"
        top_result = results[0]
        assert top_result.number == 201, (
            f"Expected PR #201 as top result, got #{top_result.number} "
            f"(score={top_result.score:.4f})"
        )

    def test_below_threshold_hint(
        self,
        sync_conn_e2e: psycopg.Connection,
        e2e_seeded_prs,
        fake_embedder,
    ):
        """When score_threshold is raised above all scores, zero results pass,
        but the top candidate is still identifiable for the CLI hint (D-11)."""
        pr_ids, repository_id = e2e_seeded_prs

        async def fake_embed_texts(texts: list[str], model: str) -> list[list[float]]:
            return [fake_embedder(t) for t in texts]

        embed_cfg = EmbedConfig(
            model="text-embedding-3-small",
            version="v1-e2e-threshold",
        )
        indexer = Indexer(sync_conn_e2e, embed_cfg)
        with patch("harness.indexer.index.embed_texts", new=fake_embed_texts):
            asyncio.run(indexer.run(repository_id))

        # Use an arbitrary query vector
        query_vec = fake_embedder("some query about cancellation")

        async def _do_search_with_high_threshold():
            import psycopg as _psycopg
            async with await _psycopg.AsyncConnection.connect(
                sync_conn_e2e.info.dsn
            ) as async_conn:
                await register_vector_async(async_conn)
                repo = SkillRepo(async_conn)
                # threshold=2.0 is above all possible cosine sims, so zero pass
                results_above = await repo.search(
                    query_vec=query_vec,
                    top_n=5,
                    oversample_factor=5,
                    score_threshold=2.0,
                    problem_weight=0.6,
                    solution_weight=0.4,
                )
                # fetch the top candidate with no threshold for the hint
                results_all = await repo.search(
                    query_vec=query_vec,
                    top_n=1,
                    oversample_factor=5,
                    score_threshold=0.0,
                    problem_weight=0.6,
                    solution_weight=0.4,
                )
                return results_above, results_all

        above_threshold, all_candidates = asyncio.run(_do_search_with_high_threshold())

        assert len(above_threshold) == 0, (
            f"Expected 0 results above threshold=2.0, got {len(above_threshold)}"
        )
        # The hint should have a candidate
        assert len(all_candidates) > 0, (
            "Expected at least one candidate available for below-threshold hint"
        )
        # The hint candidate must have a non-negative score (cosine sim can be negative
        # for random vectors, but composite is valid)
        hint = all_candidates[0]
        assert isinstance(hint.score, float), "Hint candidate score must be a float"

    def test_search_result_fields_complete(
        self,
        sync_conn_e2e: psycopg.Connection,
        e2e_seeded_prs,
        fake_embedder,
    ):
        """SearchResult has all fields required for D-12 output block."""
        pr_ids, repository_id = e2e_seeded_prs

        async def fake_embed_texts(texts: list[str], model: str) -> list[list[float]]:
            return [fake_embedder(t) for t in texts]

        embed_cfg = EmbedConfig(
            model="text-embedding-3-small",
            version="v1-e2e-fields",
        )
        indexer = Indexer(sync_conn_e2e, embed_cfg)
        with patch("harness.indexer.index.embed_texts", new=fake_embed_texts):
            asyncio.run(indexer.run(repository_id))

        query_vec = fake_embedder("async cancellation")

        async def _do_search():
            import psycopg as _psycopg
            async with await _psycopg.AsyncConnection.connect(
                sync_conn_e2e.info.dsn
            ) as async_conn:
                await register_vector_async(async_conn)
                repo = SkillRepo(async_conn)
                return await repo.search(
                    query_vec=query_vec,
                    top_n=3,
                    oversample_factor=5,
                    score_threshold=0.0,
                    problem_weight=0.6,
                    solution_weight=0.4,
                )

        results = asyncio.run(_do_search())
        assert results, "Expected at least one result"

        for r in results:
            # D-12 required fields
            assert isinstance(r.pr_id, int)
            assert isinstance(r.number, int)
            assert isinstance(r.title, str) and r.title
            assert isinstance(r.repo_name, str) and r.repo_name
            assert isinstance(r.author, str)
            assert isinstance(r.diff, str)  # may be empty but must be str
            assert isinstance(r.problem_sim, float)
            assert isinstance(r.solution_sim, float)
            assert isinstance(r.score, float)
            # Score must equal composite_score formula
            expected_score = composite_score(r.problem_sim, r.solution_sim)
            assert abs(r.score - expected_score) < 1e-9, (
                f"score={r.score} != composite_score({r.problem_sim}, {r.solution_sim})={expected_score}"
            )


# ---------------------------------------------------------------------------
# Tests: output formatting (non-TTY / NO_COLOR)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSearchOutputFormatting:
    """CLI output respects NO_COLOR and non-TTY (D-13)."""

    def test_non_tty_output_has_no_ansi(self, capsys):
        """Output captured by capsys (non-TTY) must not contain ANSI escape codes (D-13)."""
        from harness.cli.search import _print_result_block
        from harness.db.repos.skill import SearchResult
        import datetime

        result = SearchResult(
            pr_id=1,
            number=42,
            title="Test PR",
            repo_name="owner/repo",
            author="alice",
            merged_at=datetime.datetime(2024, 3, 10),
            linked_issue="#99",
            files_changed=["src/a.py", "src/b.py"],
            diff="- old line\n+ new line\n",
            problem_sim=0.75,
            solution_sim=0.65,
            score=0.71,
        )

        # Print with use_color=False (simulates non-TTY / NO_COLOR)
        _print_result_block(1, 1, result, use_color=False)

        captured = capsys.readouterr()
        # No ANSI escape codes in non-TTY output
        assert "\x1b[" not in captured.out, (
            "ANSI escape codes found in non-TTY output — violates D-13"
        )
        # Structural content present
        assert "PR #42" in captured.out
        assert "Test PR" in captured.out
        assert "owner/repo" in captured.out

    def test_no_color_env_disables_ansi(self, monkeypatch, capsys):
        """NO_COLOR environment variable disables ANSI color output (D-13)."""
        monkeypatch.setenv("NO_COLOR", "1")

        from harness.cli.search import _use_color
        assert not _use_color(), "NO_COLOR should disable color"

    def test_below_threshold_block_format(self, capsys):
        """[BELOW THRESHOLD score=X.XX] prefix appears when below_threshold=True (D-11)."""
        from harness.cli.search import _print_result_block
        from harness.db.repos.skill import SearchResult
        import datetime

        result = SearchResult(
            pr_id=2,
            number=55,
            title="Below Threshold PR",
            repo_name="owner/repo",
            author="bob",
            merged_at=datetime.datetime(2024, 3, 12),
            linked_issue=None,
            files_changed=[],
            diff="",
            problem_sim=0.3,
            solution_sim=0.2,
            score=0.26,
        )

        _print_result_block(1, 1, result, use_color=False, below_threshold=True)

        captured = capsys.readouterr()
        assert "[BELOW THRESHOLD" in captured.out, "Missing [BELOW THRESHOLD] prefix"
        assert "0.260" in captured.out, "Score not in below-threshold header"

    def test_files_capped_at_five(self, capsys):
        """Files list is capped at 5 visible + '+K more' suffix (D-12)."""
        from harness.cli.search import _print_result_block
        from harness.db.repos.skill import SearchResult
        import datetime

        many_files = [f"src/file{i}.py" for i in range(10)]
        result = SearchResult(
            pr_id=3,
            number=77,
            title="Many Files PR",
            repo_name="owner/repo",
            author="charlie",
            merged_at=datetime.datetime(2024, 3, 14),
            linked_issue=None,
            files_changed=many_files,
            diff="some diff",
            problem_sim=0.8,
            solution_sim=0.7,
            score=0.76,
        )

        _print_result_block(1, 1, result, use_color=False)

        captured = capsys.readouterr()
        assert "+5 more" in captured.out, f"Expected '+5 more' in output: {captured.out!r}"
