# Production Readiness — Open Gates (Phase 3 ingest)

**Status: gate #1 CLOSED (fix live-validated 2026-06-10).**

**Phase 4 preconditions:** (1) gate #1 — CLOSED (this doc); (2) a **held-out
evaluation set** (real queries + expected-PR judgements, set aside before any
tuning) MUST exist before Phase 4 touches scoring weights — otherwise weights
are tuned and "validated" on the same data and the tuning is unfalsifiable.
Phase 3 passed automated unit verification (7/7 must-haves, 231 unit tests), but live
testing found resume silently losing ~50% of the window on interruption (gate #1 /
BUG C). Unit tests mocked the PostgreSQL transaction and the GitHub API and missed
all three live bugs (A/B/C — all now fixed; C's fix proven both by real-DB
integration tests and a live interrupted-resume run, see gate #1).

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
- **`senrah repos`** runs against the live efcore DB and lists the repo + scope +
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
- **`senrah repos` cosmetic:** a repo row that exists but never advanced the cursor
  shows "-" for the cursor cell, not "(never run)" (which only triggers when there
  is no repositories row at all). Consider showing "(never run)" when
  cursor_merged_at is NULL.

## Gates (in priority order)

### 1. Resumable incremental ingest — DATA-LOSS · **CLOSED (live-validated 2026-06-10)**
A real interrupted-and-resumed `senrah ingest` against efcore **lost 13 of 27
window PRs** (silent, permanent; proven 2026-06-09). Resume was NOT correct and
gated Phase 4. Two preconditions were found and fixed first; the third was the
real blocker:

- **Fixed (commit `bca12af`)** — `rate_limit_status` crashed the first ingest (BUG A).
- **Fixed (commit `344c49b`)** — per-PR commits were savepoints, not commits, so a
  crash rolled back the whole run (BUG B). Now durable: a mid-run kill keeps the
  committed prefix (S1=14 survived, `cursor == max(stored)`, no bogus rows).
- **Fixed (commit `cdeff2e` + probe-before-size reorder; live-validated below)** —
  design (BUG C, was the blocker): `advance_cursor` stored a **GREATEST
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
  high-water mark** surfaced by `senrah repos`; it bounds **nothing**. Resume
  correctness is owned entirely by re-scanning the configured scope window every
  run + skipping PRs already present in `pull_requests`. (Reading the cursor as a
  "processed-up-to-here" boundary was the root of C; nothing in the read path may.)
- **Traversal:** bounded by the scope `since` (config), never by the cursor. Every
  run re-scans the scope window (updated-desc, break at `updated_at < since`).
- **Probe:** `PRRepo.exists(repository_id, number)` after the (free) bot filter and
  **before `RawPR.size()`** — strictly *present-in-DB*, never a cursor compare. An
  already-ingested PR costs **neither the diff fetch nor the per-PR completion GET**
  (size() fires the completion GET; running it after the probe keeps the Finding-2
  N+1 from coming back on every re-scan). A PR missed on a prior run (interrupt OR
  per-PR error isolation) is absent → re-fetched. This recovers errored PRs for
  free — **no separate freeze-on-error machinery**.
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

**LIVE VALIDATION (2026-06-10, closes the gate).** Real interrupted-resume against
dotnet/efcore on a fresh DB (scope `since_date 2026-06-03`, window = 23 PRs):
- Run killed at 8/23 committed, with `cursor_merged_at` already at the window
  maximum (#38367) — the exact worst-case BUG C condition (high-water cursor races
  ahead of coverage on the updated-desc scan).
- Resume: `15 upserted, 8 already-present` — the probe skipped all of S1 with zero
  repeat diff fetches.
- Clean uninterrupted reference run on a truncated DB: `23 upserted`; the final PR
  set is **identical** to the interrupted+resumed set (Compare-Object: no diff).
  **0 PRs lost** (vs 13/27 before the fix).
- Filters live too: `filtered 20 bot / 1 giant` on both runs (also a live data
  point for gate #3's stderr observability line).

### 2. `senrah init` live flow + YAML structure preservation
With a live `GITHUB_TOKEN` and a real repo: token validates (token-free
accept/reject), the entry is merged into `senrah.yaml` **without** destroying
comments or the `embed:`/`search:`/`mcp:` blocks, and `senrah repos` reflects it.
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

- `pull_requests.files_changed` stays `[]` for ingested PRs (the giant filter uses
  the cheap int count, not the list).

## Resolved by the gate-#1 redesign (2026-06-10) — were deferrals

- ~~`overlap_margin` tunable floor vs run-duration derivation~~ — **moot**:
  `overlap_margin` is removed entirely (full scope re-scan + probe subsumes its
  only job). No migration-0003 run-duration column needed.
- ~~Same-second cursor boundary (strict `merged_at <=` against the cursor drops a
  same-second sibling)~~ — **resolved by inclusive-since, confirmed in code**: no
  cursor compare exists anywhere in the read path, and the scope bound is
  boundary-inclusive (`if pr.merged_at < since: continue` in
  `connectors/github.py` — both the created-asc spine and the updated-desc scan),
  so a PR with `merged_at == since` stays in the window and same-second siblings
  are both re-encountered; the probe/idempotent upsert dedups.

_Mirror of `.planning/phases/03-production-ingestion/03-HUMAN-UAT.md` (which is
gitignored). Last updated 2026-06-10._
