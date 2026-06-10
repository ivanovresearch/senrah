# Production Readiness — Open Gates (Phase 3 ingest)

**Status: BLOCKED on gate #1 (data-loss on resume, proven 2026-06-09).** Phase 3
passed automated unit verification (7/7 must-haves, 231 unit tests), but live testing
found resume silently loses ~50% of the window on interruption (see gate #1). Unit
tests mocked the PostgreSQL transaction and the GitHub API and missed all three live
bugs (A/B fixed, C is the blocker).

**Phase 4 precondition:** gate #1 (BUG C) MUST be fixed before Phase 4 (tuning).
Tuning scoring/weights on a corpus that drops half the window on any interruption is
meaningless — the corpus is unpredictably holey. This is a design change (cursor
semantics), not a "run it on Docker" item.

A `[x]` for Phase 3 in `.planning/ROADMAP.md` means "all plans have a SUMMARY",
**not** "verified on live infra". Do not read it as production-ready.

## Validated on real infra (2026-06-08, Docker up, DB loaded with 100 dotnet/efcore PRs)

- **Integration suite (real pgvector Postgres via testcontainers):** `test_resume`
  (cursor stored after first PR, second run starts after cursor, advance_cursor
  monotonic), `test_migration_0002`, `test_migrations` all PASS. → Gate #1's core
  mechanic (atomic cursor + resume) is now validated on a **real database**, not
  just mocks. 11 other integration tests fail, but only from a pre-existing
  **test-isolation defect** (session-scoped DB container, no per-test truncation —
  rows accumulate across tests); each passes in isolation. Unrelated to Phase 3.
- **`harness repos`** runs against the live efcore DB and lists the repo + scope +
  (blank) op-state. → OPS-02 validated on real data.

## Real-infra findings (fix before relying on production ingest)

- **MIGRATION DRIFT:** the loaded DB had the 100 PRs but was still at migration
  **0001** — the op-state columns (cursor_merged_at, …) were missing until
  `alembic upgrade head` was run by hand (0001→0002). The unit-verified code cannot
  detect this; any deploy MUST run `alembic upgrade head` before a cursor-based
  ingest, or `advance_cursor` fails on a real DB.
- **alembic CLI ignores `.env`:** `alembic.ini` has `sqlalchemy.url = ${DATABASE_URL}`
  but the alembic CLI does not load `.env` (only pydantic EnvSettings does), so
  `DATABASE_URL` must be exported in the shell before running alembic. Document this
  (or add dotenv loading to `alembic/env.py`).
- **`harness repos` cosmetic:** a repo row that exists but never advanced the cursor
  shows "-" for the cursor cell, not "(never run)" (which only triggers when there
  is no repositories row at all). Consider showing "(never run)" when
  cursor_merged_at is NULL.

## Gates (in priority order)

### 1. Resumable incremental ingest — DATA-LOSS · **BLOCKER (proven 2026-06-09)**
A real interrupted-and-resumed `harness ingest` against efcore **lost 13 of 27
window PRs** (silent, permanent). Resume is NOT correct. This GATES Phase 4 (see
below). Two preconditions were found and fixed first; the third is the real blocker:

- **Fixed (commit `bca12af`)** — `rate_limit_status` crashed the first ingest (BUG A).
- **Fixed (commit `344c49b`)** — per-PR commits were savepoints, not commits, so a
  crash rolled back the whole run (BUG B). Now durable: a mid-run kill keeps the
  committed prefix (S1=14 survived, `cursor == max(stored)`, no bogus rows).
- **UNFIXED — design (BUG C, the blocker):** `advance_cursor` stores a **GREATEST
  high-water mark**, not a **contiguous "processed-up-to-here" low-watermark**. The
  incremental scan commits PRs in `updated`-desc order, so the cursor reaches the
  newest `merged_at` on the *first* committed PR while ~half the window is still
  unprocessed. On resume, the window filter `merged_at > cursor − overlap_margin`
  drops every unprocessed PR older than the last `overlap_margin` (1h) → permanent
  skip. Measured: |S_clean|=27, after kill+resume |S2|=14, **13 skipped**
  (`38192 38260 38269 38286 38291 38293 38297 38304 38307 38321 38340 38342 38359`).

**This is the SAME residual hole documented in `03-FINDINGS-traversal.md`, re-assessed:**
it was logged there as an edge/rare "delayed-visibility" case mitigated by
`overlap_margin`. That under-scoped it. The real exposure is **~50% of the window on
*any* mid-run interruption**, because the high-water cursor races ahead of actual
coverage — not a rare timing edge. Lesson: a documented-but-under-scoped debt is
still a blocker; "rare" was wrong.

**Root to fix (next session, fresh context):** make the cursor a contiguous
low-watermark (advance only past a prefix with no unprocessed older-in-window PR
behind it), OR process the window oldest-first, OR have resume re-scan the full scope
window ignoring the high-water cursor until caught up. The fix must target the
cursor *semantics* (high-water → contiguous), not the scan order alone.

**RESOLUTION (2026-06-10) — full-scope re-scan + present-in-DB probe (chosen over
low-watermark).** The low-watermark's only advantage (cheap steady-state on
*unbounded* history) never materialises here — scopes are bounded (last_n /
since_date / period) — and its cost is two new classes of silent state-bug
(bimodal reset-on-completion + freeze-on-error) of the same family as A/B/C. So:

- **Cursor semantics (now, one line):** `cursor_merged_at` is a **diagnostic
  high-water mark** surfaced by `harness repos`; it bounds **nothing**. Resume
  correctness is owned entirely by re-scanning the configured scope window every
  run + skipping PRs already present in `pull_requests`. (Reading the cursor as a
  "processed-up-to-here" boundary was the root of C; nothing in the read path may.)
- **Traversal:** bounded by the scope `since` (config), never by the cursor. Every
  run re-scans the scope window (updated-desc, break at `updated_at < since`).
- **Probe:** `PRRepo.exists(repository_id, number)` immediately before `fetch_diff`
  — strictly *present-in-DB*, never a cursor compare. An already-ingested PR costs
  zero diff fetch; a PR missed on a prior run (interrupt OR per-PR error isolation)
  is absent → re-fetched. This recovers errored PRs for free — **no separate
  freeze-on-error machinery**.
- **`overlap_margin`: DEAD — removed.** Its only job was re-yielding a drift-skipped
  PR for the idempotent upsert to dedup (at the cost of a repeat diff fetch every
  run). The full scope re-scan re-encounters any such PR, and the probe makes the
  re-encounter free. Removed from the connector, the Ingester, config, and the
  `IngestFilterConfig` knob. (It was a fixed-width masking layer — exactly the kind
  we already rejected as a non-fix for C.)
- **`--backfill`:** now inert for traversal (every run already re-scans the scope);
  retained for CLI compatibility — use scope `all` for a deep re-enumeration.

Proven by two real-DB tests (testcontainer, autocommit as in `cli/ingest`, real
traversal + advance_cursor, only `fetch_diff`/`rate_limit_status` stubbed), each
RED before the fix and GREEN after:
`tests/integration/test_resume.py::TestResumeDataLossBugC` (BUG C) and
`::TestResumeRecoversErroredPR` (errored-PR recovery).

### 2. `harness init` live flow + YAML structure preservation
With a live `GITHUB_TOKEN` and a real repo: token validates (token-free
accept/reject), the entry is merged into `harness.yaml` **without** destroying
comments or the `embed:`/`search:`/`mcp:` blocks, and `harness repos` reflects it.
The ruamel round-trip is unit-tested on synthetic YAML only.

### 3. Bot/giant filtering observability on a real repo
Against a repo with bot-authored and oversized PRs: the per-run stderr line reports
non-zero `filtered N bot / M giant`, and no bot/giant PR is stored. Filter predicates
are unit-tested; the end-to-end exclusion-before-diff-fetch path is not run live.

### 4. Rate-limit proactive backoff on a real pipeline
Near the rate-limit floor: the proactive throttle fires (stderr pause-until-reset
line) and the run continues without losing the committed cursor. The `Retry-After`
wait and floor check are unit-tested in isolation only.

## Accepted deferrals (lower priority, recorded in `.planning/phases/03-production-ingestion/03-FINDINGS-traversal.md`)

- `overlap_margin` is a tunable floor, not the run-duration derivation (needs a
  persisted run-duration column / migration 0003). MVP-accepted.
- `pull_requests.files_changed` stays `[]` for ingested PRs (the giant filter uses
  the cheap int count, not the list).
- Same-second cursor boundary: strict `merged_at <=` can drop a same-second sibling;
  fix = `(merged_at, number)` tiebreak.

_Mirror of `.planning/phases/03-production-ingestion/03-HUMAN-UAT.md` (which is
gitignored). Last updated 2026-06-08._
