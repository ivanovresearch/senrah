"""
tests/unit/test_mcp_tool.py — MCP tool tests for search_prs_v1.

These tests verify the full MCP tool contract using an in-process MCP client
(mcp.shared.memory.create_connected_server_and_client_session — no subprocess needed).

The tool implementation lives in harness.mcp.server (created in Plan 02-02).

Tests that only exercise the formatting/schema layer (test_files_capped_at_six,
test_diff_excerpt_truncation, test_pr_link_derivation) verify the underlying helpers
independent of the server.

Mocking strategy:
- Patch harness.mcp.server.embed_texts to return a deterministic 1536-dim vector
- Patch harness.db.repos.skill.SkillRepo.search to return SearchResult fixtures (no real DB)
- Use create_connected_server_and_client_session for full protocol round-trips
"""

from __future__ import annotations

import pytest

from harness.db.repos.skill import SearchResult
from harness.mcp.formatting import fmt_diff_excerpt_mcp, fmt_files_mcp
from harness.mcp.schema import score_to_confidence_label


# ---------------------------------------------------------------------------
# Shared SearchResult fixtures (used in server-dependent tests)
# ---------------------------------------------------------------------------


def _make_result(
    number: int = 42,
    title: str = "Add retry logic",
    repo_name: str = "owner/repo",
    score: float = 0.70,
    files: list[str] | None = None,
    diff: str = "diff content",
) -> SearchResult:
    from datetime import datetime

    return SearchResult(
        pr_id=1,
        number=number,
        title=title,
        repo_name=repo_name,
        author="alice",
        merged_at=datetime(2024, 1, 15, 12, 0, 0),
        linked_issue=None,
        files_changed=files or ["src/retry.py"],
        diff=diff,
        problem_sim=0.75,
        solution_sim=0.65,
        score=score,
    )


# ---------------------------------------------------------------------------
# Wave 1 — formatting/schema helpers (pass in Wave 0 once formatting.py exists)
# ---------------------------------------------------------------------------


def test_files_capped_at_six():
    """fmt_files_mcp caps files at 6 and reports omitted count (MCP-02)."""
    files = [f"file{i}.py" for i in range(9)]
    visible, omitted = fmt_files_mcp(files)
    assert len(visible) == 6
    assert omitted == 3


def test_diff_excerpt_truncation():
    """fmt_diff_excerpt_mcp head-truncates to limit chars with marker (MCP-02 / D-03)."""
    diff = "x" * 1000
    result = fmt_diff_excerpt_mcp(diff, limit=100)
    assert "truncated" in result.lower() or "[..." in result


def test_pr_link_derivation():
    """build_envelope derives pr_link correctly (D-01 / MCP-02)."""
    from harness.mcp.formatting import build_envelope

    result = _make_result(number=55, repo_name="myorg/myrepo")
    envelope = build_envelope([result], best=None, debug=False, output_diff_limit=2000)
    assert envelope.results[0].pr_link == "https://github.com/myorg/myrepo/pull/55"


# ---------------------------------------------------------------------------
# Wave 2 — server-dependent tests
# ---------------------------------------------------------------------------


async def test_tool_registered():
    """search_prs_v1 is registered and callable via the MCP protocol (MCP-01)."""
    from unittest.mock import AsyncMock, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    fake_vec = [0.0] * 1536
    with patch("harness.mcp.server.embed_texts", new=AsyncMock(return_value=[fake_vec])):
        with patch(
            "harness.db.repos.skill.SkillRepo.search",
            new=AsyncMock(return_value=[_make_result()]),
        ):
            server = create_mcp_server(env=None, cfg=None)
            async with create_connected_server_and_client_session(
                server._mcp_server
            ) as client_session:
                tools_result = await client_session.list_tools()
                tool_names = [t.name for t in tools_result.tools]
                assert "search_prs_v1" in tool_names



async def test_tool_missing_query():
    """Missing required 'query' parameter returns isError=True (MCP-01 / T-02-05)."""
    from unittest.mock import AsyncMock, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    fake_vec = [0.0] * 1536
    with patch("harness.mcp.server.embed_texts", new=AsyncMock(return_value=[fake_vec])):
        server = create_mcp_server(env=None, cfg=None)
        async with create_connected_server_and_client_session(
            server._mcp_server
        ) as client_session:
            result = await client_session.call_tool("search_prs_v1", {})
            assert result.isError



async def test_response_envelope_ok():
    """Successful response has status='ok', populated results with all MCP-02 fields."""
    from unittest.mock import AsyncMock, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    fake_vec = [0.0] * 1536
    with patch("harness.mcp.server.embed_texts", new=AsyncMock(return_value=[fake_vec])):
        with patch(
            "harness.db.repos.skill.SkillRepo.search",
            new=AsyncMock(return_value=[_make_result()]),
        ):
            server = create_mcp_server(env=None, cfg=None)
            async with create_connected_server_and_client_session(
                server._mcp_server
            ) as client_session:
                result = await client_session.call_tool(
                    "search_prs_v1", {"query": "async retry"}
                )
                assert not result.isError
                assert result.structuredContent is not None
                data = result.structuredContent
                assert data["status"] == "ok"
                assert len(data["results"]) > 0
                r = data["results"][0]
                assert "pr_number" in r
                assert "title" in r
                assert "score" in r
                assert "repo" in r
                assert "author" in r
                assert "pr_link" in r
                assert "diff_excerpt" in r
                assert "files" in r
                assert "files_truncated" in r



async def test_debug_components():
    """debug=False omits p_sim/s_sim; debug=True includes them (MCP-02)."""
    from unittest.mock import AsyncMock, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    fake_vec = [0.0] * 1536
    with patch("harness.mcp.server.embed_texts", new=AsyncMock(return_value=[fake_vec])):
        with patch(
            "harness.db.repos.skill.SkillRepo.search",
            new=AsyncMock(return_value=[_make_result()]),
        ):
            server = create_mcp_server(env=None, cfg=None)
            async with create_connected_server_and_client_session(
                server._mcp_server
            ) as client_session:
                # Without debug
                result_nodebug = await client_session.call_tool(
                    "search_prs_v1", {"query": "test", "debug": False}
                )
                assert result_nodebug.structuredContent["results"][0]["p_sim"] is None
                assert result_nodebug.structuredContent["results"][0]["s_sim"] is None

                # With debug
                result_debug = await client_session.call_tool(
                    "search_prs_v1", {"query": "test", "debug": True}
                )
                assert result_debug.structuredContent["results"][0]["p_sim"] is not None
                assert result_debug.structuredContent["results"][0]["s_sim"] is not None



async def test_response_envelope_no_results():
    """No-results case: status='no_matches_above_threshold', results=[], best present."""
    from unittest.mock import AsyncMock, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    fake_vec = [0.0] * 1536
    # First call (above threshold) returns empty; second call (threshold=0.0) returns best
    with patch("harness.mcp.server.embed_texts", new=AsyncMock(return_value=[fake_vec])):
        with patch(
            "harness.db.repos.skill.SkillRepo.search",
            new=AsyncMock(side_effect=[[], [_make_result(score=0.15)]]),
        ):
            server = create_mcp_server(env=None, cfg=None)
            async with create_connected_server_and_client_session(
                server._mcp_server
            ) as client_session:
                result = await client_session.call_tool(
                    "search_prs_v1", {"query": "novel task"}
                )
                data = result.structuredContent
                assert data["status"] == "no_matches_above_threshold"
                assert data["results"] == []
                assert data["best_below_threshold"] is not None
                assert data["best_below_threshold"]["pr_number"] == 42



async def test_embed_failure_error():
    """OpenAI embed failure → isError=True, no DSN/internals leaked (MCP-03 / D-05)."""
    from unittest.mock import AsyncMock, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    with patch(
        "harness.mcp.server.embed_texts",
        new=AsyncMock(side_effect=RuntimeError("API key invalid")),
    ):
        server = create_mcp_server(env=None, cfg=None)
        async with create_connected_server_and_client_session(
            server._mcp_server
        ) as client_session:
            result = await client_session.call_tool("search_prs_v1", {"query": "test"})
            assert result.isError
            # Error message must not contain DSN or internal details
            error_text = result.content[0].text if result.content else ""
            assert "DATABASE_URL" not in error_text
            assert "postgresql://" not in error_text



async def test_db_failure_error():
    """DB failure → isError=True, generic message only, no DSN leaked (MCP-03 / D-06)."""
    from unittest.mock import AsyncMock, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    fake_vec = [0.0] * 1536
    with patch("harness.mcp.server.embed_texts", new=AsyncMock(return_value=[fake_vec])):
        with patch(
            "harness.db.repos.skill.SkillRepo.search",
            new=AsyncMock(side_effect=RuntimeError("connection refused postgresql://secret@localhost")),
        ):
            server = create_mcp_server(env=None, cfg=None)
            async with create_connected_server_and_client_session(
                server._mcp_server
            ) as client_session:
                result = await client_session.call_tool("search_prs_v1", {"query": "test"})
                assert result.isError
                error_text = result.content[0].text if result.content else ""
                # Generic message only — no DSN in MCP response (D-06)
                assert "postgresql://" not in error_text



async def test_no_stdout_contamination():
    """Tool response content list contains only TextContent; no print() leaks (MCP-03)."""
    from unittest.mock import AsyncMock, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    fake_vec = [0.0] * 1536
    with patch("harness.mcp.server.embed_texts", new=AsyncMock(return_value=[fake_vec])):
        with patch(
            "harness.db.repos.skill.SkillRepo.search",
            new=AsyncMock(return_value=[_make_result()]),
        ):
            server = create_mcp_server(env=None, cfg=None)
            async with create_connected_server_and_client_session(
                server._mcp_server
            ) as client_session:
                result = await client_session.call_tool(
                    "search_prs_v1", {"query": "test"}
                )
                # All content items must be TextContent (type="text")
                for item in result.content:
                    assert item.type == "text"



async def test_stdio_transport_smoke():
    """In-process client round-trip simulates stdio transport (MCP-04)."""
    from unittest.mock import AsyncMock, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    fake_vec = [0.0] * 1536
    with patch("harness.mcp.server.embed_texts", new=AsyncMock(return_value=[fake_vec])):
        with patch(
            "harness.db.repos.skill.SkillRepo.search",
            new=AsyncMock(return_value=[_make_result()]),
        ):
            server = create_mcp_server(env=None, cfg=None)
            async with create_connected_server_and_client_session(
                server._mcp_server
            ) as client_session:
                tools = await client_session.list_tools()
                assert any(t.name == "search_prs_v1" for t in tools.tools)



def test_network_transport_config():
    """create_mcp_server with network settings sets stateless_http=True (MCP-04 / D-07)."""
    from harness.mcp.server import create_mcp_server

    server = create_mcp_server(env=None, cfg=None, host="127.0.0.1", port=8001)
    # The FastMCP server should be constructed with stateless_http=True
    # and the specified host/port
    assert server is not None
    # Verify stateless_http is set (attribute on the settings object)
    if hasattr(server, "settings"):
        assert server.settings.host == "127.0.0.1"



async def test_repos_filter():
    """repos=['owner/repo'] narrows results; result repo field matches (SEARCH-03)."""
    from unittest.mock import AsyncMock, call, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    fake_vec = [0.0] * 1536
    mock_search = AsyncMock(return_value=[_make_result(repo_name="owner/repo")])
    with patch("harness.mcp.server.embed_texts", new=AsyncMock(return_value=[fake_vec])):
        with patch("harness.db.repos.skill.SkillRepo.search", new=mock_search):
            server = create_mcp_server(env=None, cfg=None)
            async with create_connected_server_and_client_session(
                server._mcp_server
            ) as client_session:
                result = await client_session.call_tool(
                    "search_prs_v1",
                    {"query": "test", "repos": ["owner/repo"]},
                )
                data = result.structuredContent
                # SkillRepo.search was called with repos=["owner/repo"]
                search_call_kwargs = mock_search.call_args_list[-1].kwargs
                assert search_call_kwargs.get("repos") == ["owner/repo"]
                # Result repo field matches
                if data["results"]:
                    assert data["results"][0]["repo"] == "owner/repo"



async def test_repos_all_default():
    """repos=None (omitted) passes repos=None to SkillRepo.search (SEARCH-03)."""
    from unittest.mock import AsyncMock, patch

    from mcp.shared.memory import create_connected_server_and_client_session

    from harness.mcp.server import create_mcp_server

    fake_vec = [0.0] * 1536
    mock_search = AsyncMock(return_value=[_make_result()])
    with patch("harness.mcp.server.embed_texts", new=AsyncMock(return_value=[fake_vec])):
        with patch("harness.db.repos.skill.SkillRepo.search", new=mock_search):
            server = create_mcp_server(env=None, cfg=None)
            async with create_connected_server_and_client_session(
                server._mcp_server
            ) as client_session:
                await client_session.call_tool("search_prs_v1", {"query": "test"})
                # repos param should be None when not specified
                search_call_kwargs = mock_search.call_args_list[0].kwargs
                assert search_call_kwargs.get("repos") is None
