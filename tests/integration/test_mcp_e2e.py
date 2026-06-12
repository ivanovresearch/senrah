"""
tests/integration/test_mcp_e2e.py — full-stack E2E through search_prs_v1.

QUAL-03 / Phase-5 SC4: the existing walking-skeleton E2E asserts
SkillRepo.search against the real pgvector container; the MCP-layer tests
mock the DB. This test closes the gap between them: a real MCP protocol
round-trip (in-memory client session) against the REAL container DB —
seed fixture PRs → index with the fake embedder → call search_prs_v1 →
expected top PR comes back through the tool envelope.

Only embed_texts is patched (both at index and at query time, with the SAME
deterministic fake embedder). SkillRepo, the async pool, and the FastMCP
server are all real. No real GitHub token or OpenAI key is used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import psycopg
import pytest
from pgvector.psycopg import register_vector

from harness.config import EnvSettings, McpConfig, SearchConfig, YamlConfig
from harness.db.models import Project, PullRequest, Repository
from harness.db.repos.pr import PRRepo
from harness.db.repos.project import ProjectRepo
from harness.db.repos.repository import RepositoryRepo

TARGET_TITLE = "Fix async cancellation token propagation in runtime"
TARGET_BODY = "The async runtime was not propagating cancellation tokens correctly."


@pytest.fixture
def mcp_e2e_db(pg_dsn_migrated: str, fake_embedder):
    """Seed 3 PRs and index them with the fake embedder; return the DSN."""
    import asyncio as _asyncio

    from harness.config import EmbedConfig
    from harness.indexer.index import Indexer

    conn = psycopg.connect(pg_dsn_migrated)
    register_vector(conn)
    conn.autocommit = True

    project = ProjectRepo(conn).upsert(Project(name="mcp-e2e-project"))
    repo = RepositoryRepo(conn).upsert(
        Repository(project_id=project.id, type="github", name="mcp-e2e/repo")
    )
    pr_repo = PRRepo(conn)
    for number, title, body, diff in [
        (301, TARGET_TITLE, TARGET_BODY, "+ Task.Run(() => Work(), token);\n"),
        (302, "Add cursor pagination", "Cursor pagination for API.", "+ GetPage(cursor)\n"),
        (303, "Refactor pool init", "Factory pattern for pool.", "+ PoolFactory.Create()\n"),
    ]:
        pr_repo.upsert(
            PullRequest(
                repository_id=repo.id,
                number=number,
                title=title,
                body=body,
                diff=diff,
                author="alice",
                merged_at=datetime(2024, 3, 10, tzinfo=timezone.utc),
            )
        )

    async def fake_embed_texts(texts, model, **kwargs):
        return [fake_embedder(t) for t in texts]

    with patch("harness.indexer.index.embed_texts", new=fake_embed_texts):
        indexed = _asyncio.run(
            Indexer(conn, EmbedConfig(model="m", version="v-e2e")).run(repo.id)
        )
    conn.close()
    assert indexed == 3
    return pg_dsn_migrated


async def test_search_prs_v1_full_stack(mcp_e2e_db: str, fake_embedder):
    """search_prs_v1 over a real pool + real pgvector DB returns the target PR."""
    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.indexer.embedder import build_problem_text
    from harness.mcp.server import create_mcp_server

    env = EnvSettings(
        database_url=mcp_e2e_db,
        github_token="fake-not-used",
        openai_api_key="fake-not-used",
    )
    cfg = YamlConfig(
        search=SearchConfig(top_n=3, score_threshold=0.0),
        mcp=McpConfig(),
    )

    # Query with the TARGET's exact problem text: the fake embedder is
    # hash-seeded, so the query vector equals PR 301's problem embedding.
    query = build_problem_text(TARGET_TITLE, TARGET_BODY)

    async def fake_embed_texts(texts, model, **kwargs):
        return [fake_embedder(t) for t in texts]

    with patch("harness.mcp.server.embed_texts", new=fake_embed_texts):
        server = create_mcp_server(env=env, cfg=cfg)
        async with create_connected_server_and_client_session(
            server._mcp_server
        ) as client_session:
            result = await client_session.call_tool(
                "search_prs_v1", {"query": query}
            )

    assert not result.isError
    data = result.structuredContent
    assert data["status"] == "ok"
    assert data["results"], "no results from real DB"
    top = data["results"][0]
    assert top["pr_number"] == 301, f"expected PR 301 on top, got {top['pr_number']}"
    assert top["repo"] == "mcp-e2e/repo"
    assert "diff_excerpt" in top and top["diff_excerpt"]
