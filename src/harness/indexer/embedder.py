"""
harness.indexer.embedder — tiktoken token-based truncation + batched AsyncOpenAI.

Provides:
- truncate_to_tokens(text, limit) -> str
  Head-priority truncation using cl100k_base encoding (D-06/D-07/Pitfall 3/A4).
  Logs a WARNING with token counts only when truncation occurs (T-03-04).
- build_problem_text(title, body) -> str
  Concatenates title + body for problem embedding input (D-07).
- embed_texts(texts, model) -> list[list[float]]
  Batched AsyncOpenAI embedding call (RESEARCH Pattern 6).

Boundary constraints (STATE.md):
- NO SQL, no pgvector operators, no DB access.  All DB access lives in db/repos/.
- Logging goes to stderr only (logger.warning → stderr via logging framework).
  Never use print() — MCP stdio cleanliness (Phase 2 preparation).
"""

from __future__ import annotations

import logging
from typing import Any

import tiktoken
from openai import AsyncOpenAI

# Module-level logger — writes to stderr via standard logging (anti-print pattern)
logger = logging.getLogger(__name__)

# cl100k_base is the correct encoding for text-embedding-3-small (Pitfall 3 / A4)
# Module-level singleton: encoding is expensive to instantiate, reuse it.
_enc = tiktoken.get_encoding("cl100k_base")

# Maximum texts per single embeddings API call (A2 assumption: 2048 limit).
# Conservative value gives headroom against undocumented per-call limits.
_BATCH_SIZE = 2048


# ---------------------------------------------------------------------------
# Token-based truncation (D-06 / D-07)
# ---------------------------------------------------------------------------


def truncate_to_tokens(text: str, limit: int) -> str:
    """Head-priority truncation: return first `limit` tokens of `text`.

    Truncation is measured in MODEL TOKENS via tiktoken cl100k_base — never
    in characters (D-06).  Title-first ordering means the most signal-dense
    content is preserved (head-priority for problem text, per D-07).

    When truncation occurs, a WARNING is emitted with the original and
    truncated token counts plus the percentage dropped.  The warning contains
    ONLY token counts — never the actual text content (T-03-04: information
    disclosure mitigation; safe for Phase 2 MCP stdio cleanliness).

    Args:
        text: Input text (may be arbitrarily long).
        limit: Maximum number of tokens to keep.

    Returns:
        The original text if token count ≤ limit; otherwise the decoded
        first `limit` tokens.
    """
    tokens = _enc.encode(text)
    original_count = len(tokens)

    if original_count <= limit:
        return text

    # Compute ratio for the log message (T-03-04: counts only, no text)
    ratio_dropped = (original_count - limit) / original_count
    logger.warning(
        "Text truncated: %d → %d tokens (%.0f%% dropped)",
        original_count,
        limit,
        ratio_dropped * 100,
    )

    return _enc.decode(tokens[:limit])


# ---------------------------------------------------------------------------
# Problem text builder (D-07)
# ---------------------------------------------------------------------------


def build_problem_text(title: str, body: str) -> str:
    """Concatenate title and body for the problem embedding.

    Format: "title\\n\\nbody" stripped of leading/trailing whitespace.
    This preserves the most important signal (title) at the head (D-07
    head-priority), which matters when the combined text exceeds
    problem_limit_tokens and truncation is applied.

    Args:
        title: PR title.
        body: PR description / body (may be empty string).

    Returns:
        Combined problem text, stripped.
    """
    return f"{title}\n\n{body}".strip()


# ---------------------------------------------------------------------------
# Batched async embedding (RESEARCH Pattern 6)
# ---------------------------------------------------------------------------


async def embed_texts(
    texts: list[str],
    model: str = "text-embedding-3-small",
    api_key: str | None = None,
    base_url: str | None = None,
) -> list[list[float]]:
    """Embed a list of texts using an OpenAI-compatible API, in input order.

    The OpenAI API accepts up to _BATCH_SIZE inputs per call (A2 — 2048 verified).
    Large lists are split into batches and the results are re-assembled in order.

    Provider-agnostic: `base_url` lets the same OpenAI client target any
    OpenAI-compatible embeddings endpoint (e.g. OpenRouter at
    https://openrouter.ai/api/v1, Azure OpenAI, or a local server). When
    `base_url` is None the SDK default (api.openai.com) is used. The embedding
    model MUST still return 1536-dim vectors to match the skills.vector(1536)
    schema (e.g. text-embedding-3-small, or openai/text-embedding-3-small on
    OpenRouter).

    Args:
        texts: List of strings to embed.  Each must be within the model token limit
               (truncate_to_tokens should be applied before calling this function).
        model: Embedding model name.  Defaults to text-embedding-3-small (vector(1536)).
        api_key: API key for the provider.  When None, the SDK falls back to the
                 OPENAI_API_KEY environment variable (T-03-01: secret from ENV only).
        base_url: OpenAI-compatible endpoint base URL.  When None, the SDK default
                  (OpenAI) is used.

    Returns:
        List of embedding vectors, one per input text, in the same order.
    """
    if not texts:
        return []

    # Instantiate per-call (stateless; avoids shared mutable state across tasks).
    # Only pass api_key / base_url when provided so the SDK's ENV-based defaults
    # still apply otherwise (T-03-01: key never logged, never in config).
    client_kwargs: dict[str, Any] = {}
    if api_key:
        client_kwargs["api_key"] = api_key
    if base_url:
        client_kwargs["base_url"] = base_url
    client = AsyncOpenAI(**client_kwargs)

    all_embeddings: list[list[float]] = []

    # Process in batches to stay within API limits (T-03-05: DoS mitigation)
    for batch_start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[batch_start : batch_start + _BATCH_SIZE]
        response = await client.embeddings.create(
            model=model,
            input=batch,
        )
        # response.data is guaranteed to be ordered to match input order
        all_embeddings.extend(item.embedding for item in response.data)

    return all_embeddings
