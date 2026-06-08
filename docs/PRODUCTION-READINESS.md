# Production Readiness — Open Gates (Phase 3 ingest)

**Status: PARTIALLY validated on real infra (2026-06-08).** Phase 3 passed automated
unit verification (7/7 must-haves, 231 unit tests). The unit tests mock the
**PostgreSQL transaction** and the **GitHub API**; the gates below close that gap.

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

### 1. Resumable incremental ingest — DATA-CORRUPTION RISK · PARTIALLY validated
The per-PR `upsert + advance_cursor` runs inside one `conn.transaction()`, and resume
reads the stored cursor. **Now validated at the repo layer on a real Postgres**
(`test_resume.py`: cursor stored after first PR, second run starts after cursor,
advance_cursor monotonic — all PASS via testcontainers). **Still unproven: a full
end-to-end `harness ingest` run interrupted mid-stream on real GitHub data**, confirming
the re-run resumes from the cursor with no double-fetch and no skip. Until that runs,
treat production ingest as not fully proven.

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
