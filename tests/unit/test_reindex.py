"""
tests/unit/test_reindex.py — Phase 4: `index --reindex` + truncation context.

INDEX-03: Indexer.run(reindex=True) deletes the repository's skills rows
first, then rebuilds all embeddings from the raw store. No connector is
involved anywhere in the indexer (no GitHub at reindex time, by module
design).

INDEX-04: the truncation WARNING names the PR number and the field
(problem/diff) plus original/truncated token counts — and never the text
itself (T-03-04).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

from harness.config import EmbedConfig
from harness.db.models import PullRequest
from harness.indexer.embedder import truncate_to_tokens
from harness.indexer.index import Indexer


def _pr(pr_id: int, number: int, diff: str = "diff --git a/f b/f\n+x\n") -> PullRequest:
    return PullRequest(
        id=pr_id,
        repository_id=1,
        number=number,
        title=f"PR {number}",
        body="body",
        diff=diff,
        author="alice",
        merged_at=None,
    )


async def _fake_embed_texts(texts, model, **kwargs):
    return [[0.0] * 1536 for _ in texts]


class TestReindex:
    def test_reindex_deletes_then_rebuilds_all(self) -> None:
        cfg = EmbedConfig(model="text-embedding-3-small", version="v2")
        indexer = Indexer(MagicMock(), cfg)

        with patch("harness.indexer.index.PRRepo") as MockPRRepo, patch(
            "harness.indexer.index.SkillRepo"
        ) as MockSkillRepo, patch(
            "harness.indexer.index.embed_texts", new=_fake_embed_texts
        ):
            MockSkillRepo.return_value.delete_for_repository.return_value = 3
            MockPRRepo.return_value.unindexed_prs.return_value = [
                _pr(1, 101),
                _pr(2, 102),
                _pr(3, 103),
            ]

            count = asyncio.run(indexer.run(7, reindex=True))

        MockSkillRepo.return_value.delete_for_repository.assert_called_once_with(7)
        assert count == 3
        # All rows rewritten under the configured version
        for call in MockSkillRepo.return_value.upsert_skill.call_args_list:
            assert call.kwargs["version"] == "v2"

    def test_default_run_does_not_delete(self) -> None:
        cfg = EmbedConfig(model="text-embedding-3-small", version="v1")
        indexer = Indexer(MagicMock(), cfg)

        with patch("harness.indexer.index.PRRepo") as MockPRRepo, patch(
            "harness.indexer.index.SkillRepo"
        ) as MockSkillRepo, patch(
            "harness.indexer.index.embed_texts", new=_fake_embed_texts
        ):
            MockPRRepo.return_value.unindexed_prs.return_value = [_pr(1, 101)]
            asyncio.run(indexer.run(7))

        MockSkillRepo.return_value.delete_for_repository.assert_not_called()

    def test_indexer_module_has_no_connector_dependency(self) -> None:
        """INDEX-03 criterion: no GitHub API calls during reindex — enforced
        structurally: the indexer module never imports a connector."""
        import harness.indexer.index as mod

        src = open(mod.__file__, encoding="utf-8").read()
        import_lines = [
            line for line in src.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        assert not any("connector" in line.lower() for line in import_lines), (
            f"indexer imports a connector: {import_lines}"
        )


class TestTruncationContext:
    def test_warning_names_pr_and_field(self, caplog) -> None:
        long_text = "word " * 5000
        with caplog.at_level(logging.WARNING):
            truncate_to_tokens(long_text, 100, context="PR #38140 diff")
        record = next(r for r in caplog.records if "truncated" in r.getMessage())
        msg = record.getMessage()
        assert "PR #38140 diff" in msg
        assert "100" in msg  # truncated count
        # T-03-04: counts only — the content must never leak into the log
        assert "word word" not in msg

    def test_no_warning_under_limit(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            truncate_to_tokens("short", 100, context="PR #1 problem")
        assert not [r for r in caplog.records if "truncated" in r.getMessage()]

    def test_indexer_passes_context(self, caplog) -> None:
        cfg = EmbedConfig(
            model="text-embedding-3-small",
            version="v1",
            problem_limit_tokens=10,
            diff_limit_tokens=10,
        )
        indexer = Indexer(MagicMock(), cfg)
        big = "token " * 200

        with patch("harness.indexer.index.PRRepo") as MockPRRepo, patch(
            "harness.indexer.index.SkillRepo"
        ), patch("harness.indexer.index.embed_texts", new=_fake_embed_texts):
            MockPRRepo.return_value.unindexed_prs.return_value = [
                _pr(1, 4242, diff=big)
            ]
            with caplog.at_level(logging.WARNING):
                asyncio.run(indexer.run(1))

        messages = [r.getMessage() for r in caplog.records if "truncated" in r.getMessage()]
        assert any("PR #4242 diff" in m for m in messages)
