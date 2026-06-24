---
gsd_state_version: 1.0
milestone: v1.2
milestone_name: Corpus Depth
status: executing
last_updated: "2026-06-24T15:33:13.277Z"
last_activity: 2026-06-24
progress:
  total_phases: 3
  completed_phases: 1
  total_plans: 9
  completed_plans: 5
  percent: 33
---

# State: Senrah

**Project:** Senrah
**Milestone:** v1.2 — Corpus Depth (roadmap created 2026-06-22)
**Initialized:** 2026-05-30

---

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-22 after v1.2 scoping)

**Core Value:** An AI agent solving a task in this codebase can retrieve the most relevant real merged-PR precedents (problem + the diff that solved it) via MCP, ranked by semantic similarity. If everything else fails, that retrieval must work.

**Current Focus:** Phase 10 — temporal-holdout-harness-multi-year-ingest

---

## Current Position

Phase: 10 (temporal-holdout-harness-multi-year-ingest) — EXECUTING
Plan: 2 of 5
Status: Executing Phase 10
Last activity: 2026-06-24 -- 10-01 complete (Wave-0 stubs)

## Performance Metrics

| Metric | Value |
|--------|-------|
| Phases complete | 0 / 3 (v1.2) |
| Requirements delivered | 1 / 13 (v1.2) — JUDGE-01 |
| Plans complete | 5 / 9 (Phase 09 x4 + Phase 10 plan 01) |
| Unit tests | 318 passing (302 + 16 new stubs/xfail) |

---

## v1.2 Roadmap (Phases 9–11)

The hard dependency drives the order: **Eval v3 (EVAL-*) must land and freeze before the depth experiment (DEPTH-*) runs** — multi-year ingest multiplies backports, so an un-deduped deep corpus makes the depth delta uninterpretable. The deliverable is a *trustworthy measurement* (a defensible decision-gate result), not a particular outcome.

| Phase | Goal | Requirements |
|-------|------|--------------|
| 9 — Eval v3: Trustworthy Deduped Scale | Deduped, triaged, re-frozen eval scale + blind judge calibration exist and are frozen before any depth measurement | EVAL-01, EVAL-02, EVAL-03, EVAL-04, JUDGE-01 |
| 10 — Temporal-Holdout Harness + Multi-Year Ingest | The real leak-free hit-rate@k instrument exists and the deep corpus is ingested; every rung materializes from one ingest + one index | DEPTH-01, DEPTH-02, DEPTH-03, DEPTH-04 |
| 11 — Depth Ladder + Judge + Decision Gate | The depth experiment runs and a trustworthy synchronized decision-gate conclusion is recorded | DEPTH-05, DEPTH-06, DEPTH-07, JUDGE-02 (conditional) |

**Architecture invariant (do not violate):** the only `src/` change all milestone is one additive, `None`-default `merged_at` window param on `SkillRepo.search` (DEPTH-02), never exposed through the MCP tool. MCP server and connector seam are untouched, no migration (`merged_at` already present). All eval/judge machinery lives in `eval/`; `anthropic` goes in a `[project.optional-dependencies] eval` extra so `pip install senrah` stays LLM-free.

**Open v1.2 decisions to resolve at planning:**

- Phase 9: diff-similarity threshold for backport detection (project decision, not from literature); the agreed Cohen's κ floor for judge authority.
- Phase 10: exact T and rung floors (3.5mo / 1yr / 2–3yr) — derivable only from the real `merged_at` span after the full ingest.
- Phase 11: post-dedup holdout query count → statistical power (conclusive vs merely directional) is the first thing to check; whether corpus-level grouping must become DB-resident (migration 0004) only if the holdout query-set shape forces it.

---

## Accumulated Context

### Key Decisions Logged

| Decision | Rationale | Phase |
|----------|-----------|-------|
| Use HNSW (not IVFFlat) from Day 1 | IVFFlat on empty table = zero recall; HNSW correct for continuous ingest | 1 |
| All logs to stderr, stdout reserved for JSON-RPC | MCP stdio transport: any stdout output corrupts protocol stream | 2 |
| `SCORE_THRESHOLD` default 0.25-0.30 | Cosine sim for code rarely exceeds 0.80; 0.50+ silently drops all broad-query results | 1 |
| Cursor written to DB after each committed PR | In-memory cursors lost on crash; must be durable to enable resumability | 3 |
| `.env` git-ignored + `.env.example` before first push | Open-source repo; secret commit requires immediate rotation + history scrub | 1 |
| `ConnectorProtocol` as `typing.Protocol` | New source needs no changes to Indexer or MCP; zero ABC overhead | 1 |
| Raw diff persisted in `pull_requests` | Enables diff excerpt in MCP output and `--reindex` without re-fetching GitHub | 1 |
| GitHub 403 = secondary rate limit (not just 429) | Must handle `Retry-After` on 403; code checking only `x-ratelimit-remaining` misses this | 3 |
| HNSW indexes use vector_cosine_ops (not vector_l2_ops) | Must match <=> cosine query operator; mismatch causes silent Seq Scan (Pitfall 1) | 1 |
| files_changed stored as JSONB in pull_requests | Self-describing; psycopg3 serializes list[str] natively; avoids re-fetch at query time | 1 |
| load_yaml_config rejects secret keys | Prevents accidentally putting DATABASE_URL/tokens in YAML; enforces ENV-only posture | 1 |
| setuptools.build_meta backend | setuptools.backends.legacy was not available; build_meta is the standard backend | 1 |
| Linked-issue regex uses clos(?:es?|e)|fix(?:es?)?|resolv(?:es?|e) | Research Pattern 3 regex was wrong for singular "fix"; fixed to correctly match fix/fixes/close/closes/resolve/resolves | 1 |
| files_changed uses json.dumps+::jsonb in parameterized SQL | psycopg3 does not auto-serialize plain Python list as JSONB in parameterized INSERT; explicit cast required | 1 |
| Ingester composition root pattern | GitHubConnector instantiated only in cli/ingest.py; Ingester accepts ConnectorProtocol only | 1 |
| tiktoken cl100k_base module-level singleton | Encoding expensive to instantiate; reuse at module level; correct for text-embedding-3-small (D-06/Pitfall 3) | 1 |
| Truncation log counts-only (T-03-04) | Warning contains original→truncated token counts, never text content; safe for MCP stdio cleanliness | 1 |
| Indexer interleaved text list | [pr0_problem, pr0_solution, ...] enables single batch embed_texts call; index arithmetic re-associates results | 1 |
| SkillRepo ON CONFLICT (pr_id, model, version) | Re-indexing same model/version updates in place; different model/version creates new row (D-08 per-row persistence) | 1 |
| score_to_confidence_label bands at 0.45/0.65 (weak/moderate/strong) | Calibrated to text-embedding-3-small practical ceiling ~0.80 on code; D-01 single source of truth for structured and text confidence | 2 |
| merged_at typed Optional[str] in PRResultV1; converted in build_envelope | Pitfall 4 prevention: no datetime serialization ambiguity on the wire | 2 |
| fmt_files_mcp returns (list[str], int) tuple | Structured field stays machine-readable; +K more string only in text rendering | 2 |
| create_mcp_server factory pattern (env=None _StubPool) | env=None test path uses _StubPool so SkillRepo.search patches work without real DB; keeps module import side-effect-free | 2 |
| ToolError re-raise guard in DB except block | except ToolError: raise prevents embed ToolError from being re-wrapped as generic DB error | 2 |
| Scope is frozen dataclass (immutable value object) | mode+value pairs; frozen prevents accidental mutation of config-derived scope values | 3 |
| resolve_since is I/O-free (last_n_merged_at_provider is an iterable) | Unit-testable without network; Ingester supplies the data source; clean seam | 3 |
| upsert_repo_entry guards ruamel import (T-03-SC) | config.py stays importable before Plan 04 installs ruamel.yaml | 3 |
| advance_cursor uses GREATEST (monotonic cursor) | Out-of-order older merge cannot move high-water mark backward (D-B3 correctness) | 3 |
| Design B: incremental traversal uses updated-desc + early break (not created-asc full scan) | created-asc spine re-paginates entire history every incremental run (O(full history)); merge bumps updated_at so updated-desc + break at cursor-margin catches all new merges and stops at the window | 3 |
| overlap_margin derivation is Ingester policy, connector only applies it | Margin = max(floor, last_run_duration × safety_factor) needs op-state run timing; keeps connector mechanism-only | 3 |
| list_recent_merged_meta uses true top-N by merged_at (updated-desc + heap), not created-desc proxy | created-desc ≠ merged-desc; proxy gives wrong last_n window lower bound (the false-premise class of bug) | 3 |
| Connector metadata read is N+1 (1 GET/PR for additions/deletions), paid only for yielded PRs | additions/deletions absent from list payload → CompletableGithubObject completion GET; verified at Requester layer | 3 |
| Same-second cursor boundary can silently drop a sibling (strict merged_at <= ); deferred | second-granular merged_at + merged_at-only filter; fix = (merged_at, number) tiebreak — recorded in 03-FINDINGS | 3 |
| Ingester filters bot/giant on cheap metadata BEFORE fetch_diff (per-PR atomic upsert+advance_cursor in one transaction) | excluded PRs incur zero diff fetches (INGEST-03 structural); crash loses at most the in-flight PR (D-B3) | 3 |
| RawPR.changed_files (int) added for giant-by-file count | Design B yields files_changed=[]; without the int the giant filter silently no-ops in production | 3 |
| overlap_margin = tunable floor (ingest.overlap_margin_seconds 3600s), run-duration derivation deferred | op-state has no run-duration column; full derivation needs migration 0003 — accepted floor for MVP (user decision) | 3 |
| _binary_collapse frozenset must include "relevant" (already-binary) to avoid double-collapse | Test fixtures use already-binary "relevant" grade; excluding it collapses all relevant pairs to "irrelevant" -> kappa always 1.0 | 9 |
| Wave-0 stubs use module-level skip for missing modules (bootstrap_ci, define_split) and xfail(strict=False) for missing params (merged_before/after); vacuous if-guards replaced with assert-presence | if-guard pattern silently passes when params absent, producing XPASS instead of XFAIL -- defeat the purpose of Wave-0 stubs | 10 |
| grade_fn resolved via sys.modules at _score_gold call time to support monkeypatch | Direct function reference at call site bypasses monkeypatch; sys.modules lookup at call time enables test stubs without dependency injection | 9 |
| anthropic imported lazily in grade_pair (not at module top) | eval.judge.judge must be importable in tests without pip install senrah[eval]; deferred import is the idiomatic pattern | 9 |
| Per-cluster deduplication: hit on any cluster member = one cluster hit; distractors per-cluster (EVAL-02 / D-08) | Divergence fixture demonstrates per-PR=2 vs per-cluster=1 for two cluster members in top-k | 9 |
| Stage-2 triage final: 2 duplicate (Stage-1 auto), 17 real-fail, 0 label-error (EVAL-03 / D-09) | Conservative stance on 37194 (no positive evidence 37359 ranked, frozen store has only top1) under D-11 no-silent-number-tuning | 9 |
| v3 deduped baseline: recall@1=0.711, recall@5=0.899, recall@10=0.927, MRR@10=0.794 (EVAL-04) | 3 misses recovered (37762, 37474, 37194) via cluster grouping counting any member as a hit; movement is structural (per-cluster scoring), not number tuning (D-11) | 9 |
| run_eval.py requires explicit --manifest flag; silent tag-to-filename fallback is now loud | A missing manifest-v3-deduped.json silently re-used v2 and froze a wrong baseline; explicit --manifest + loud fallback prevents T-09-06 re-freezing | 9 |

### Architectural Constraints (do not violate)

- All SQL and pgvector operators live exclusively in `db/repos/` — no SQL leaks into Indexer, MCP Server, or CLI
- MCP Server is read-only and stateless; it never mutates any table and never contacts GitHub
- Connector knows nothing about DB schema or embeddings
- Scoring formula is a pure function in `scoring.py`, shared by Indexer and MCP Server
- Secrets only from environment variables; no coupling to any secret backend
- **v1.2:** the only `src/` change is the additive `None`-default `merged_at` window param on `SkillRepo.search` (DEPTH-02), never exposed through the MCP tool — no contract bump, no migration. All eval/judge machinery stays in `eval/`; `anthropic` lives only in the `[project.optional-dependencies] eval` extra.

### Open Todos

- (none open at v1.1 close)

### Blockers

- (none) — the v1.0 Docker Desktop blocker is moot: the integration suite now runs server-side in CI on a `pgvector/pgvector:pg17` testcontainer (Phase 6, green). Local Docker runs remain optional.

---

## Session Continuity

### Last Session

- **Date:** 2026-06-24
- **Action:** Completed 10-01-PLAN.md: Wave-0 test stubs for DEPTH-02/03/04 (4 files, 2 commits).
- **Outcome:** 4 stub files created (test_skill_repo_search_window, test_bootstrap_ci, test_temporal_split, test_skill_repo_window). All stubs xfail or skip cleanly. 318 unit tests pass (302 existing + 0 broken). Integration stubs xfail correctly. Plans 02/05 can now reference these files.

### Resumption Prompt

> Phase 09 complete (EVAL-01..04 + JUDGE-01). The trustworthy deduped baseline exists. Phase 10 (Temporal-Holdout Harness + Multi-Year Ingest) is planned — 5 plans (10-01..10-05) written and reviewed. Start execution with /gsd-execute-phase 10. Note: 10-03 (1–2h `--scope all` ingest) and 10-04 (D-05 T-choice) are blocking human checkpoints.

---

## Phase Completion Log

| Phase | Completed | Requirements Delivered |
|-------|-----------|------------------------|
| 1–5 (v1.0) | 2026-05-31 → 2026-06-12 | 31/31 v1.0 |
| 6–8 (v1.1) | 2026-06-14 → 2026-06-18 | 11/11 v1.1 |
| 9–11 (v1.2) | (pending) | 0/13 v1.2 |

---

*State initialized: 2026-05-30*
*Last updated: 2026-06-22 after v1.2 roadmap creation*

## Deferred Items

Items acknowledged and deferred at v1.0 milestone close on 2026-06-12:

| Category | Item | Status | Justification |
|----------|------|--------|---------------|
| uat | 03-HUMAN-UAT scenario 4: rate-limit proactive throttle live firing | deferred | Not reproducible on demand (requires burning ~4900 API calls to approach the floor). Substitute coverage accepted: respx-mocked Retry-After backoff (test_diff_retry.py), throttle floor/wait unit tests (test_throttle.py), live runs up to ~1200 req/window without incident. |

## Operator Next Steps

- Execute Phase 10 with `/gsd-execute-phase 10` (10-03 and 10-04 are blocking human checkpoints — multi-year ingest + T-choice).
