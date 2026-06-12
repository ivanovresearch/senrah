# QUAL-01..04 — line-by-line verification (Phase 5 DoD)

Audited 2026-06-12 against the literal wording in `.planning/REQUIREMENTS.md`
and Phase 5 success criteria 3–5 in `.planning/ROADMAP.md`. Full suite at
audit time: **297 passed** (unit + integration, one run, no deselections).

## QUAL-01 — "Unit tests cover scoring, truncation, and filtering" ✅

- **Scoring** — `tests/unit/test_scoring.py` (12 tests): canonical formula
  output, default 0.6/0.4 weights, weight dominance both directions,
  explicit-weight override, zero/equal sims, float return, no-DB/no-deps
  structural guards. SC3 "correct scoring formula output": covered.
- **Truncation** — `tests/unit/test_embedder.py` (17 tests): head-priority
  boundaries (`test_exactly_at_limit_unchanged`, sub-limit unchanged),
  token-not-char measurement (`*_via_tiktoken_not_chars`, `cl100k_base_used`),
  warning emission with counts and WITHOUT content (T-03-04). Plus
  `tests/unit/test_reindex.py::TestTruncationContext` (INDEX-04 labels).
  SC3 "correct head-priority truncation boundaries": covered.
- **Filtering** — `tests/unit/test_filters.py` (predicate boundaries:
  100 files NOT giant, 5000 lines NOT giant, `[bot]` suffix, stop-list),
  `tests/unit/test_ingest_filtering.py` (pipeline level: giant excluded
  BEFORE diff fetch, bot excluded, automation-title excluded before probe),
  `tests/unit/test_diff_files.py`. SC3 "bot/giant-PR filter exclusions":
  covered.

## QUAL-02 — "Connector tests run against a mocked GitHub API (respx)" ✅ with recorded deviation

- respx is used wherever the connector speaks **httpx**: diff fetch +
  Retry-After backoff (`test_diff_retry.py`), credential validation
  (`test_validate_creds.py`), connector fetch paths
  (`test_github_connector.py`).
- **Deviation (recorded in `03-FINDINGS-traversal.md`):** PyGithub uses
  `requests`, not httpx — respx PHYSICALLY cannot intercept the PR-list
  endpoints. Those are tested at PyGithub's `Requester` seam instead
  (`test_traversal_incremental.py` counts real GET attempts at that layer —
  strictly stronger than respx for the N+1 class it was built to catch;
  `test_connector_traversal.py` for traversal contracts).
- SC3 "no real network calls": holds across the whole unit suite (mocks at
  respx or Requester layer; no test requires a token).
- Verdict: the intent (mocked GitHub, no network) is fully met; the literal
  "via respx" is met on every surface where respx can apply.

## QUAL-03 — "E2E against a real test repository (testcontainers pgvector)" ✅ (gap found and closed in this audit)

- `tests/integration/test_end_to_end.py`: real `pgvector/pgvector:pg17`
  container, fixture PRs seeded, indexed via fake embedder,
  `SkillRepo.search` returns the expected top PR; below-threshold hint path
  covered.
- **Gap found by this audit:** Phase-5 SC4 says the E2E must assert
  **`search_prs_v1`** returns the expected top result — the old E2E stopped
  at `SkillRepo.search`, and the MCP-layer tests mocked the DB. **Closed:**
  `tests/integration/test_mcp_e2e.py` does a real MCP protocol round-trip
  (in-memory client session, real FastMCP server, real async pool, real
  container DB) — seed → index → `call_tool("search_prs_v1")` → expected
  top PR in the structured envelope. Only `embed_texts` is faked.
- SC4 "no real GitHub token or OpenAI key": both E2E tests run with fake
  embedder and dummy credentials.

## QUAL-04 — "gitleaks pre-commit gate; no real secrets anywhere" ✅

- `.githooks/pre-commit` (commit `2d65167`) runs gitleaks over staged
  changes on every commit; FAILS CLOSED if the scanner is missing.
  Enable per clone: `git config core.hooksPath .githooks`.
- Verified live both directions: a realistic `ghp_…` token blocked the
  commit (exit 1, file+line reported); clean commits pass. Caveat recorded:
  gitleaks allowlists obviously-fake sequential-alphabet strings — test the
  gate with realistic tokens.
- Full-history scan (58 commits at gate installation): no leaks.
  `tests/unit/test_secrets_hygiene.py` additionally guards the source tree.
- The real per-user `harness.yaml` is untracked + gitignored
  (`harness.yaml.example` is the committed template) — closing the most
  likely landing spot for a "temporary" token.
