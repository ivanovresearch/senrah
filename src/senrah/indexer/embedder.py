"""
senrah.indexer.embedder — tiktoken token-based truncation + batched AsyncOpenAI.

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

# Maximum TOKENS per single embeddings API call. OpenAI-compatible endpoints
# cap total tokens per request (~300k for OpenAI); some providers (incl.
# OpenRouter) return HTTP 200 with an empty `data` array when the cap is
# exceeded, which the OpenAI SDK surfaces as "No embedding data received".
# Batching by input count alone is insufficient — a single batch of large
# diffs (each up to diff_limit_tokens) can blow past the cap. Use a
# conservative per-request token budget well under the provider ceiling.
_MAX_BATCH_TOKENS = 100_000


def _token_batches(texts: list[str]) -> list[list[str]]:
    """Split texts into batches bounded by BOTH input count and total tokens.

    Each batch has at most _BATCH_SIZE inputs AND at most _MAX_BATCH_TOKENS
    tokens (counted via the same cl100k_base encoding used for truncation).
    A single text is never split (callers truncate to <= diff_limit_tokens
    first, which is far below the per-request budget).
    """
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for text in texts:
        text_tokens = len(_enc.encode(text))
        would_exceed_tokens = current and (current_tokens + text_tokens > _MAX_BATCH_TOKENS)
        would_exceed_count = len(current) >= _BATCH_SIZE
        if would_exceed_tokens or would_exceed_count:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(text)
        current_tokens += text_tokens
    if current:
        batches.append(current)
    return batches


# ---------------------------------------------------------------------------
# Token-based truncation (D-06 / D-07)
# ---------------------------------------------------------------------------


def truncate_to_tokens(text: str, limit: int, context: str = "") -> str:
    """Head-priority truncation: return first `limit` tokens of `text`.

    Truncation is measured in MODEL TOKENS via tiktoken cl100k_base — never
    in characters (D-06).  Title-first ordering means the most signal-dense
    content is preserved (head-priority for problem text, per D-07).

    When truncation occurs, a WARNING is emitted with the original and
    truncated token counts plus the percentage dropped.  The warning contains
    ONLY token counts and the caller-supplied context label (e.g.
    "PR #38140 diff" — INDEX-04: operators must see WHICH PR and WHICH field
    lost signal) — never the actual text content (T-03-04: information
    disclosure mitigation; safe for Phase 2 MCP stdio cleanliness).

    Args:
        text: Input text (may be arbitrarily long).
        limit: Maximum number of tokens to keep.
        context: Optional label identifying the text (PR number + field).

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
        "Text truncated%s: %d → %d tokens (%.0f%% dropped)",
        f" [{context}]" if context else "",
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

    # Sanitize empty/whitespace-only inputs. The embeddings API rejects empty
    # strings, and some providers (OpenRouter) respond to a batch containing one
    # with HTTP 200 + null data, poisoning every other input in that batch. A PR
    # with no diff (Phase 1 minimal ingest does not yet filter empty diffs — that
    # is INGEST-03 in Phase 3) yields an empty solution text; substitute a single
    # space so the request succeeds and input/output ordering is preserved.
    texts = [t if (t and t.strip()) else " " for t in texts]

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

    # Process in batches bounded by input count AND token budget (T-03-05: DoS
    # mitigation; also prevents the provider returning empty data on oversized
    # requests). encoding_format="float" is explicit so the SDK does not default
    # to base64 (which raises a misleading "No embedding data received" on some
    # OpenAI-compatible providers) and returns plain float lists.
    for batch in _token_batches(texts):
        response = await client.embeddings.create(
            model=model,
            input=batch,
            encoding_format="float",
        )
        # Guard: a non-erroring provider response with empty/None data must not
        # crash with an opaque "NoneType is not iterable". Surface it clearly.
        data = response.data
        if not data:
            raise RuntimeError(
                f"Embeddings API returned no data for a batch of {len(batch)} "
                f"input(s) (model={model}). The provider may have rejected the "
                f"request (e.g. token/size limit)."
            )
        # response.data is ordered to match input order
        all_embeddings.extend(item.embedding for item in data)

    return all_embeddings
