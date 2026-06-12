"""
harness.cli.search — `harness search` command.

Embeds the query text, runs SkillRepo.search (oversample ANN + Python re-rank),
and prints one result block per result to stdout.

Design decisions:
- D-10: ANN fetches top_n * oversample_factor candidates; Python re-ranks.
- D-11: If zero results clear score_threshold, the single highest-scoring
  candidate is shown with a [BELOW THRESHOLD score=X.XX] prefix.
- D-12: Full result block per result: score, PR#/title, repo, author, merged,
  linked issue, files (capped), diff excerpt — mirrors the future MCP output.
- D-13: Plain-text. Color is cosmetic only, applied when sys.stdout.isatty()
  and NO_COLOR is unset. Structural separators are static ASCII, never ANSI.

Security:
- T-04-01: query text reaches the embeddings API only; never interpolated into SQL.
           SkillRepo.search uses %(vec)s bind parameter for the query vector.
- T-04-04: PR title/diff content rendered as plain text; no ANSI injection.
- T-04-05: OpenAI key from ENV (AsyncOpenAI reads OPENAI_API_KEY automatically).

No SQL in this module — all DB access via SkillRepo (STATE.md constraint).
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional

import typer

# ---------------------------------------------------------------------------
# CLI constants
# ---------------------------------------------------------------------------

# Maximum number of files to list before showing "+K more"
_MAX_FILES_DISPLAYED = 5

# Diff excerpt character limit (keeps terminal output manageable)
_DIFF_EXCERPT_CHARS = 500


# ---------------------------------------------------------------------------
# Formatting helpers (D-12 / D-13)
# ---------------------------------------------------------------------------


def _use_color() -> bool:
    """Return True if ANSI color should be applied (D-13).

    Color is cosmetic only; never used to convey structural meaning.
    Disabled when stdout is not a TTY (piped) or NO_COLOR is set.
    """
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _fmt_files(files_changed: list[str]) -> str:
    """Format the files list, capping at _MAX_FILES_DISPLAYED (D-12)."""
    if not files_changed:
        return "(none)"
    visible = files_changed[:_MAX_FILES_DISPLAYED]
    rest = len(files_changed) - _MAX_FILES_DISPLAYED
    parts = ", ".join(visible)
    if rest > 0:
        parts += f" (+{rest} more)"
    return parts


def _fmt_diff_excerpt(diff: str) -> str:
    """Return a bounded diff excerpt (D-12)."""
    if not diff:
        return "(no diff)"
    excerpt = diff[:_DIFF_EXCERPT_CHARS]
    if len(diff) > _DIFF_EXCERPT_CHARS:
        excerpt += f"\n  [... truncated to {_DIFF_EXCERPT_CHARS} chars ...]"
    # Indent each line by 2 spaces for readability
    lines = excerpt.splitlines()
    return "\n".join(f"  {line}" for line in lines)


def _print_result_block(
    index: int,
    total: int,
    result,
    use_color: bool,
    below_threshold: bool = False,
) -> None:
    """Print one result block to stdout (D-12 / D-13).

    Args:
        index: 1-based result index.
        total: Total number of results being displayed.
        result: SearchResult instance.
        use_color: Whether to apply ANSI color codes.
        below_threshold: If True, prefix the header with [BELOW THRESHOLD].
    """
    # ANSI escape codes — only used when use_color=True (D-13).
    BOLD = "\033[1m" if use_color else ""
    DIM = "\033[2m" if use_color else ""
    YELLOW = "\033[33m" if use_color else ""
    CYAN = "\033[36m" if use_color else ""
    RESET = "\033[0m" if use_color else ""

    # Header line: separator + score
    if below_threshold:
        score_str = f"[BELOW THRESHOLD score={result.score:.3f}]"
        header = f"--- {score_str} ---"
    else:
        header = f"--- Result {index}/{total}  (score: {result.score:.3f}) ---"

    print(f"{BOLD}{YELLOW}{header}{RESET}")

    # PR title + number
    print(f"{BOLD}PR #{result.number}:{RESET} {result.title}")

    # Metadata
    merged_str = (
        result.merged_at.strftime("%Y-%m-%d")
        if result.merged_at is not None
        else "unknown"
    )
    print(f"{DIM}Repo:{RESET}   {result.repo_name}")
    print(f"{DIM}Author:{RESET} {result.author or 'unknown'}   Merged: {merged_str}")

    if result.linked_issue:
        print(f"{DIM}Issue:{RESET}  {result.linked_issue}")

    # Files changed
    files_str = _fmt_files(result.files_changed)
    print(f"{DIM}Files:{RESET}  {files_str}")

    # Diff excerpt
    print()
    print(f"{CYAN}Diff excerpt:{RESET}")
    print(_fmt_diff_excerpt(result.diff))
    print()


# ---------------------------------------------------------------------------
# Async search runner
# ---------------------------------------------------------------------------


async def _run_search(
    database_url: str,
    query: str,
    top_n: int,
    oversample_factor: int,
    score_threshold: float,
    problem_weight: float,
    solution_weight: float,
    embed_model: str,
    api_key: str | None = None,
    base_url: str | None = None,
):
    """Embed query, open async pool, call SkillRepo.search, return results.

    Returns:
        list[SearchResult] — results that cleared the threshold (may be empty).
        SearchResult | None — the top candidate below threshold, when list is empty.
    """
    from harness.db.pool import create_pool
    from harness.db.repos.skill import SkillRepo
    from harness.indexer.embedder import embed_texts

    # Embed the query once (symmetric retrieval — RESEARCH query-embedding decision).
    # The same vector is compared against both problem_embedding and solution_embedding.
    # api_key + base_url route through the configured provider (OpenAI / OpenRouter),
    # mirroring the index path; key flows from EnvSettings (ENV-only secret).
    query_vecs = await embed_texts(
        [query], model=embed_model, api_key=api_key, base_url=base_url
    )
    query_vec = query_vecs[0]

    # Open async pool + register pgvector type (create_pool handles both).
    pool = await create_pool(database_url)
    try:
        async with pool.connection() as conn:
            repo = SkillRepo(conn)
            results = await repo.search(
                query_vec=query_vec,
                top_n=top_n,
                oversample_factor=oversample_factor,
                score_threshold=score_threshold,
                problem_weight=problem_weight,
                solution_weight=solution_weight,
            )

            if results:
                return results, None

            # D-11: fetch the top candidate below threshold for the hint.
            # Re-run with threshold=0.0 and top_n=1 to get the single best.
            all_candidates = await repo.search(
                query_vec=query_vec,
                top_n=1,
                oversample_factor=oversample_factor,
                score_threshold=0.0,
                problem_weight=problem_weight,
                solution_weight=solution_weight,
            )
            top_below = all_candidates[0] if all_candidates else None
            return [], top_below
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def search_cmd(
    query: str = typer.Argument(..., help="Search query text"),
    top_n: Optional[int] = typer.Option(None, "--top-n", help="Override top_n from config"),
) -> None:
    """Search indexed PRs for the given query text.

    Embeds the query using the configured OpenAI embedding model, runs an
    oversample ANN search via the HNSW cosine index, re-ranks by composite
    score (D-09), and prints one block per result (D-12).

    If no results clear the score_threshold, the top candidate is shown with
    a [BELOW THRESHOLD] prefix and a hint to lower score_threshold (D-11).

    Color output is applied only when stdout is a TTY and NO_COLOR is unset (D-13).
    """
    from harness.config import EnvSettings, find_config_file, load_yaml_config

    # Load ENV secrets
    try:
        env = EnvSettings()
    except Exception as exc:
        typer.echo(f"ERROR: Could not load secrets from ENV: {exc}", err=True)
        raise typer.Exit(code=1)

    # Load YAML config
    cfg_path = find_config_file()
    if cfg_path is None:
        typer.echo(
            "ERROR: harness.yaml not found. "
            "Create harness.yaml in the project root (see harness.yaml.example).",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        cfg = load_yaml_config(cfg_path)
    except ValueError as exc:
        typer.echo(f"ERROR: Invalid harness.yaml: {exc}", err=True)
        raise typer.Exit(code=1)

    search_cfg = cfg.search
    effective_top_n = top_n if top_n is not None else search_cfg.top_n

    use_color = _use_color()

    try:
        results, below_threshold_candidate = asyncio.run(
            _run_search(
                database_url=env.database_url,
                query=query,
                top_n=effective_top_n,
                oversample_factor=search_cfg.oversample_factor,
                score_threshold=search_cfg.score_threshold,
                problem_weight=search_cfg.problem_weight,
                solution_weight=search_cfg.solution_weight,
                embed_model=cfg.embed.model,
                api_key=env.openai_api_key,
                base_url=cfg.embed.base_url,
            )
        )
    except Exception as exc:
        typer.echo(f"ERROR during search: {exc}", err=True)
        raise typer.Exit(code=1)

    # OPS-05: opt-in search logging (SEARCH_LOG=true); no-op by default.
    from harness.search_log import log_search

    log_search(query, len(results), source="cli")

    if results:
        total = len(results)
        for i, result in enumerate(results, start=1):
            _print_result_block(i, total, result, use_color=use_color)
    elif below_threshold_candidate is not None:
        # D-11: below-threshold hint — show top candidate with prefix
        _print_result_block(
            1, 1, below_threshold_candidate, use_color=use_color, below_threshold=True
        )
        print(
            f"[HINT] No results above score_threshold {search_cfg.score_threshold:.2f}. "
            "Lower score_threshold in harness.yaml to see more results."
        )
    else:
        print("No indexed PRs found. Run 'harness ingest' and 'harness index' first.")
