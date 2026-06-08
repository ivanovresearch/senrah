# Production Readiness — Open Gates (Phase 3 ingest)

**Status: NOT production-validated.** Phase 3 passed automated unit verification
(7/7 must-haves, 231 unit tests), but the unit tests mock the two boundaries that
matter most in production — the **PostgreSQL transaction** and the **GitHub API**.
The mechanics below have **never been run on live infrastructure**. They are core
ingest behaviors, **required before the first real ingest**, not optional polish.

A `[x]` for Phase 3 in `.planning/ROADMAP.md` means "all plans have a SUMMARY",
**not** "verified on live infra". Do not read it as production-ready.

## Gates (in priority order)

### 1. Resumable incremental ingest — DATA-CORRUPTION RISK · gate before the FIRST real ingest
The per-PR `upsert + advance_cursor` runs inside one `conn.transaction()`, and resume
reads the stored cursor. **If the real psycopg transaction/commit semantics or the
interrupt path misbehave, the cursor can move ahead of stored data → silently
skipped PRs, or duplicates.** This is corruption, not degradation. Unit tests mock
the DB connection, so atomicity and resume-after-interrupt are **unproven on a real
database**. `tests/integration/test_resume.py` exists but is Docker-gated (blocked).
**Required: run one real interrupted ingest and confirm the re-run resumes from the
cursor with no double-fetch and no skip — before trusting any production ingest.**

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
