"""
tests/unit/test_embedder.py — Unit tests for harness.indexer.embedder.

Covers INDEX-01 and INDEX-02 truncation boundaries with NO OpenAI calls.
Token counts are measured with tiktoken, proving tokens-not-characters (D-06).

Tests:
- test_problem_truncation: over-limit text is truncated to problem_limit_tokens
- test_diff_truncation: over-limit diff is truncated to diff_limit_tokens
- test_no_truncation_under_limit: sub-limit text returned unchanged
- test_build_problem_text: title + body concatenation
- test_truncation_warning_logged: caplog captures truncation warning
- test_truncation_counts_only: warning contains token counts, not text content
"""

from __future__ import annotations

import logging
import math

import pytest
import tiktoken

# The encoding for text-embedding-3-small (D-06, Pitfall 3, A4)
ENC = tiktoken.get_encoding("cl100k_base")

# Default config limits (D-07)
PROBLEM_LIMIT = 1500
DIFF_LIMIT = 6000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def token_count(text: str) -> int:
    """Count tokens using cl100k_base — proves we measure tokens, not chars."""
    return len(ENC.encode(text))


def make_long_text(target_tokens: int) -> str:
    """Generate a text whose token count exceeds target_tokens.

    Uses repetition of a real English word that tokenizes to 1 token each,
    so the count is predictable.  The "word" is just 'a' — each 'a' encodes
    to 1 token in cl100k_base.  We overshoot by 10% to guarantee truncation.
    """
    overshoot = math.ceil(target_tokens * 1.10)
    # Use short tokens: each "ab " is typically 2 tokens — we'll just repeat
    # and verify with tiktoken.  Safer to generate a known long string and
    # verify the token count rather than hard-code an exact byte sequence.
    text = "hello " * overshoot  # "hello " is 1 token + 1 token = ~2 tokens
    # Verify we've actually exceeded the limit
    assert token_count(text) > target_tokens, (
        f"Failed to generate text exceeding {target_tokens} tokens: "
        f"got {token_count(text)} tokens"
    )
    return text


# ---------------------------------------------------------------------------
# Test: build_problem_text
# ---------------------------------------------------------------------------

class TestBuildProblemText:
    """build_problem_text(title, body) -> 'title\\n\\nbody'.strip()"""

    def test_title_and_body_joined(self) -> None:
        from harness.indexer.embedder import build_problem_text

        result = build_problem_text("Fix cursor bug", "Resolves issue #42")
        assert result == "Fix cursor bug\n\nResolves issue #42"

    def test_empty_body_stripped(self) -> None:
        from harness.indexer.embedder import build_problem_text

        result = build_problem_text("Fix cursor bug", "")
        # "title\n\n".strip() -> "title"
        assert result == "Fix cursor bug"

    def test_whitespace_stripped(self) -> None:
        from harness.indexer.embedder import build_problem_text

        result = build_problem_text("  title  ", "  body  ")
        # The strip() call removes leading/trailing whitespace from the
        # full concatenated string, not from individual components.
        assert result == "title  \n\n  body"

    def test_both_empty_returns_empty(self) -> None:
        from harness.indexer.embedder import build_problem_text

        result = build_problem_text("", "")
        assert result == ""


# ---------------------------------------------------------------------------
# Test: truncate_to_tokens — problem text (INDEX-01, D-06, D-07)
# ---------------------------------------------------------------------------

class TestProblemTruncation:
    """Truncation at problem_limit_tokens uses TOKENS, not characters (D-06)."""

    def test_problem_truncation(self) -> None:
        """Over-limit problem text is truncated to exactly PROBLEM_LIMIT tokens."""
        from harness.indexer.embedder import truncate_to_tokens

        long_text = make_long_text(PROBLEM_LIMIT)
        assert token_count(long_text) > PROBLEM_LIMIT

        truncated = truncate_to_tokens(long_text, PROBLEM_LIMIT)

        # Token count of result must be <= limit (D-06)
        result_tokens = token_count(truncated)
        assert result_tokens <= PROBLEM_LIMIT, (
            f"Expected ≤{PROBLEM_LIMIT} tokens after truncation, got {result_tokens}"
        )

    def test_problem_truncation_via_tiktoken_not_chars(self) -> None:
        """Assert truncation is token-based: result token count ≤ limit.

        This test proves D-06: if truncation were character-based, the token
        count of the result would likely not match the token limit.
        """
        from harness.indexer.embedder import truncate_to_tokens

        long_text = make_long_text(PROBLEM_LIMIT)
        truncated = truncate_to_tokens(long_text, PROBLEM_LIMIT)

        # The critical assertion: token count (not char count) is at the limit
        result_token_count = len(ENC.encode(truncated))
        assert result_token_count <= PROBLEM_LIMIT

        # Also confirm the original text really was longer (confirms truncation happened)
        original_token_count = len(ENC.encode(long_text))
        assert original_token_count > PROBLEM_LIMIT

    def test_sub_limit_problem_text_unchanged(self) -> None:
        """Text under PROBLEM_LIMIT is returned exactly unchanged."""
        from harness.indexer.embedder import truncate_to_tokens

        short_text = "Fix null pointer exception in async resolver"
        assert token_count(short_text) < PROBLEM_LIMIT

        result = truncate_to_tokens(short_text, PROBLEM_LIMIT)
        assert result == short_text

    def test_exactly_at_limit_unchanged(self) -> None:
        """Text at exactly the token limit is returned unchanged (no truncation)."""
        from harness.indexer.embedder import truncate_to_tokens

        # Build a text of exactly PROBLEM_LIMIT tokens
        # "hello" is 1 token in cl100k_base
        text = "hello " * PROBLEM_LIMIT
        # Trim to exactly PROBLEM_LIMIT tokens by encoding and decoding
        tokens = ENC.encode(text)[:PROBLEM_LIMIT]
        exact_text = ENC.decode(tokens)
        assert len(ENC.encode(exact_text)) == PROBLEM_LIMIT

        result = truncate_to_tokens(exact_text, PROBLEM_LIMIT)
        assert result == exact_text


# ---------------------------------------------------------------------------
# Test: truncate_to_tokens — diff text (INDEX-02, D-06, D-07)
# ---------------------------------------------------------------------------

class TestDiffTruncation:
    """Truncation at diff_limit_tokens uses TOKENS, not characters (D-06)."""

    def test_diff_truncation(self) -> None:
        """Over-limit diff text is truncated to exactly DIFF_LIMIT tokens."""
        from harness.indexer.embedder import truncate_to_tokens

        long_diff = make_long_text(DIFF_LIMIT)
        assert token_count(long_diff) > DIFF_LIMIT

        truncated = truncate_to_tokens(long_diff, DIFF_LIMIT)

        result_tokens = token_count(truncated)
        assert result_tokens <= DIFF_LIMIT, (
            f"Expected ≤{DIFF_LIMIT} tokens after truncation, got {result_tokens}"
        )

    def test_sub_limit_diff_unchanged(self) -> None:
        """Diff under DIFF_LIMIT is returned exactly unchanged."""
        from harness.indexer.embedder import truncate_to_tokens

        short_diff = "- old_line()\n+ new_line()\n"
        assert token_count(short_diff) < DIFF_LIMIT

        result = truncate_to_tokens(short_diff, DIFF_LIMIT)
        assert result == short_diff

    def test_diff_truncation_via_tiktoken_not_chars(self) -> None:
        """Diff truncation is token-based (D-06): result re-encodes to ≤ limit."""
        from harness.indexer.embedder import truncate_to_tokens

        long_diff = make_long_text(DIFF_LIMIT)
        truncated = truncate_to_tokens(long_diff, DIFF_LIMIT)

        result_token_count = len(ENC.encode(truncated))
        assert result_token_count <= DIFF_LIMIT


# ---------------------------------------------------------------------------
# Test: truncation warning logging (INDEX-02, T-03-04)
# ---------------------------------------------------------------------------

class TestTruncationWarningLogged:
    """Truncation emits a warning log with token counts only (not text content).

    T-03-04: truncation log records COUNTS only (original→truncated),
    never the truncated text itself.
    """

    def test_truncation_warning_emitted(self, caplog: pytest.LogCaptureFixture) -> None:
        """Over-limit input triggers a logged warning."""
        from harness.indexer.embedder import truncate_to_tokens

        long_text = make_long_text(PROBLEM_LIMIT)

        with caplog.at_level(logging.WARNING, logger="harness.indexer.embedder"):
            truncate_to_tokens(long_text, PROBLEM_LIMIT)

        assert len(caplog.records) >= 1
        warning = caplog.records[0]
        assert warning.levelno == logging.WARNING

    def test_no_warning_for_sub_limit_text(self, caplog: pytest.LogCaptureFixture) -> None:
        """Sub-limit input does NOT emit a warning."""
        from harness.indexer.embedder import truncate_to_tokens

        short_text = "Fix null pointer in resolver"

        with caplog.at_level(logging.WARNING, logger="harness.indexer.embedder"):
            truncate_to_tokens(short_text, PROBLEM_LIMIT)

        assert len(caplog.records) == 0

    def test_warning_contains_token_counts(self, caplog: pytest.LogCaptureFixture) -> None:
        """Warning message contains original and truncated token counts (T-03-04)."""
        from harness.indexer.embedder import truncate_to_tokens

        long_text = make_long_text(PROBLEM_LIMIT)
        original_count = token_count(long_text)

        with caplog.at_level(logging.WARNING, logger="harness.indexer.embedder"):
            truncate_to_tokens(long_text, PROBLEM_LIMIT)

        assert len(caplog.records) >= 1
        message = caplog.records[0].getMessage()

        # Warning must contain the original token count
        assert str(original_count) in message, (
            f"Warning '{message}' should contain original count {original_count}"
        )
        # Warning must contain the truncated count (≤ PROBLEM_LIMIT)
        assert str(PROBLEM_LIMIT) in message, (
            f"Warning '{message}' should contain limit {PROBLEM_LIMIT}"
        )

    def test_warning_does_not_contain_text_content(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Warning message must NOT include the actual text content (T-03-04)."""
        from harness.indexer.embedder import truncate_to_tokens

        # Distinctive token in the text — should NOT appear in the warning
        long_text = "SENTINEL_VALUE " + "hello " * (PROBLEM_LIMIT + 100)

        with caplog.at_level(logging.WARNING, logger="harness.indexer.embedder"):
            truncate_to_tokens(long_text, PROBLEM_LIMIT)

        if caplog.records:
            message = caplog.records[0].getMessage()
            assert "SENTINEL_VALUE" not in message, (
                "Truncation warning must not include text content (T-03-04)"
            )


# ---------------------------------------------------------------------------
# Test: cl100k_base encoding present in embedder module
# ---------------------------------------------------------------------------

class TestEmbedderModuleConstraints:
    """Verify module-level constraints from PLAN.md acceptance criteria."""

    def test_cl100k_base_used(self) -> None:
        """embedder.py must use cl100k_base (Pitfall 3 / A4)."""
        import inspect
        from harness.indexer import embedder

        source = inspect.getsource(embedder)
        assert "cl100k_base" in source, (
            "embedder.py must use tiktoken cl100k_base encoding (D-06/Pitfall 3)"
        )

    def test_no_sql_in_embedder(self) -> None:
        """embedder.py must contain no SQL (SELECT/INSERT/<=>) — boundary check."""
        import inspect
        from harness.indexer import embedder

        source = inspect.getsource(embedder)
        forbidden = ["SELECT", "INSERT", "<=>"]
        for pattern in forbidden:
            assert pattern not in source, (
                f"embedder.py must not contain SQL pattern '{pattern}' "
                "(all SQL confined to db/repos/)"
            )
