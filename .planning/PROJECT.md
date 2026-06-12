# Harness

## What This Is

Harness is an open-source Python tool that indexes the merged-PR history of a codebase and serves it to AI coding agents (Claude Code, Codex, and others) over MCP. When an agent works on a task, it can pull real precedents — how similar problems were actually solved in *this* codebase — instead of guessing. It is read-only retrieval over your own version-control history: ingest merged PRs, embed problem + solution, expose a versioned `search_prs_v1` MCP tool.

## Current State (v1.0 shipped 2026-06-12)

- Full pipeline live: ingest (GitHub, multi-repo, scoped, resumable) → index (reindexable, model/version-tracked) → search (CLI + MCP stdio/streamable-HTTP).
- Working corpus: dotnet/efcore (487 PRs, ~10 months) + encode/httpx (88 PRs), 575 skills rows, uniform `text-embedding-3-small/v2`.
- Retrieval quality instrumented: frozen known-item eval (`eval/knownitem/`, 218 queries — recall@1 0.670, recall@5 0.881, MRR@10 0.760) as the regression scale for any corpus/weights/model change.
- Agent-uplift A/B run (`eval/ab/`): 12 real tasks, control vs Harness — statistical dead heat on outcomes, ZERO negative-uplift cases (the below-threshold confidence flag worked), two genuine convention-transfer wins from the deepest precedents available. Conclusion: **corpus depth, not weight tuning, is the next lever.**
- Test suite: 304 green (unit + integration incl. real-container MCP E2E); gitleaks pre-commit gate; QUAL-01..04 audited line-by-line (`docs/QUAL-AUDIT.md`).

## Core Value

An AI agent solving a task in this codebase can retrieve the most relevant real merged-PR precedents (problem + the diff that solved it) via MCP, ranked by semantic similarity. If everything else fails, that retrieval must work.

*(v1.0 check: still right. The A/B sharpened it — the value shows up as known-item retrieval ("this was fixed before") and convention transfer, both of which scale with corpus depth.)*

## Requirements

### Validated

- ✓ Full GitHub-only MVP — v1.0 (31/31 requirements, archived in `milestones/v1.0-REQUIREMENTS.md`): storage schema + HNSW, connector seam + GitHub connector, scoped/incremental/resumable ingest with filters and rate-limit handling, dual-embedding indexing with `--reindex`, weighted thresholded search with multi-repo narrowing, versioned MCP tool over stdio+network, full CLI (`init`/`repos`/`ingest`/`index`/`search`/`serve`/`status`), opt-in search logging, secrets hygiene + gitleaks, test suite.

### Active

(None — define with `/gsd:new-milestone`. Candidates surfaced by v1.0 evidence, in value order:)

- [ ] Corpus depth: `--scope all` / multi-year ingest — the A/B's identified lever; known-item eval is the before/after scale
- [ ] Known-item eval v3: dedupe/penalize backport duplicates; investigate the 19 misses for systematics
- [ ] CI: test suite + gitleaks scan server-side (gate currently per-clone opt-in)
- [ ] `harness init` upsert comment-preservation gap (drops standalone comments above rewritten keys)

### Out of Scope

- **LLM providers (Claude/Codex/local) inside harness** — harness is read-only search; agents only *consume* MCP. *(v1.0 audit: still right — the A/B used external agents cleanly.)*
- **Abstraction over other vector databases** — pgvector chosen; repository-pattern seam only. *(Still right.)*
- **GitLab / Bitbucket connectors** — behind the connector seam. *(Still right; seam held — Ingester/MCP never import the concrete connector.)*
- **Diff chunking** — single-vector with truncation; raw diff stored so chunking is reindex-only later. *(Still right, with data: 19% of solution embeddings truncate at 6000 tokens — revisit only if eval shows misses concentrate in truncated targets.)*
- **LLM summarization of long descriptions** — head-priority truncation instead. *(Still right.)*
- **Re-checking edited merged-PR descriptions** — content_hash stored, no re-verify. *(Still right.)*
- **Cross-repo score normalization** — *(v1.0 data point: two-repo corpus showed zero eval interference; keep deferred.)*
- **Search access control** — repo tag is the future seam. *(Still right.)*
- **Production hardening (backups, HA)** — Docker Postgres acceptable. *(Still right.)*
- **Weight tuning as a project priority** — NEW (from A/B): re-ranking a shallow corpus cannot create uplift; tuning is second-order until corpus depth changes what there is to rank.

## Context

- Open-source, public repository — ENV-only secrets, `.env.example`, gitleaks gate, minimal token scopes.
- Three components with distinct lifecycles (Ingestion, Indexer, MCP server) + CLI control point; raw store between ingest and index so reindex never re-fetches. This held up: the v1→v2 embedding migration and the files_changed backfill both ran entirely from the raw store.
- Evidence culture established during v1.0: unit-green ≠ working (live testing found 3 ingest bugs, an MCP heartbeat scoping bug, and 2 init input holes that mocks missed); retrieval changes get measured on the frozen eval, not eyeballed.
- ~6.5k LOC Python (src), 304 tests. Key infra: psycopg3, pgvector HNSW, FastMCP, typer, ruamel (init write path), alembic (3 migrations).

## Constraints

- **Tech stack**: Python; PostgreSQL + pgvector (HNSW on both embedding columns) — chosen, not abstracted.
- **Embeddings**: OpenAI Text Embedding 3 Small, vector(1536); ~8191 token input limit drives truncation.
- **Security**: open-source code → secrets only via environment variables; no real values anywhere in the repo; minimal token scopes documented.
- **Architecture**: connector interface is the core extensibility seam — a new source must require no changes to Indexer or MCP; MCP contract is versioned.
- **MCP server**: read-only, stateless over DB, starts independently, never touches the source at read-time.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| PostgreSQL + pgvector, no vector-DB abstraction | One chosen store; repository-pattern seam only | ✓ Good — HNSW handled 575-row corpus at ~ms latency; zero store friction all milestone |
| Persist raw diff in DB | Code output, updates, reindex without re-fetch | ✓ Good — enabled `--reindex`, files_changed backfill, and the A/B reference diffs, all with zero GitHub calls |
| solution_embedding = clean diff | Template metadata is noise for semantic search | ✓ Good — known-item recall@5 0.881 on problem+solution composite |
| Head-priority truncation, no LLM summarization | Avoids extra call, cost, failure point | ✓ Good — 19% of diffs truncate; eval shows no miss concentration there yet |
| Top-N with threshold | Don't dump irrelevant tail | ✓ Good — in the A/B the [BELOW THRESHOLD] flag produced ZERO misled-agent cases |
| Configurable weights (0.6/0.4) | Tunable per project | ⚠ Revisit framing — A/B showed tuning is second-order to corpus depth; keep configurable, don't invest |
| Versioned MCP tool name | Format changes don't break dependent prompts | ✓ Good (untested by an actual v2 yet) |
| ENV-only secrets | Open-source posture | ✓ Good — gitleaks history scan clean across all 70+ commits |
| No LLM providers in harness | Read-only search | ✓ Good |
| Incremental ingest by `merged_at` cursor | Simplest correct cursor | ✗ REVERSED (BUG C) — high-water cursor as a resume boundary lost ~50% of a window on interrupt; replaced by full-scope re-scan + present-in-DB probe; cursor demoted to diagnostic. Lesson: a documented-but-under-scoped edge is still a blocker |
| Build from spec, not prototype | Spec is source of truth | ✓ Good |
| Full-scope re-scan + probe owns resume correctness (NEW, v1.0) | Bounded scopes make re-scan cheap; probe makes it free; recovers errored PRs without extra machinery | ✓ Good — live-proven 23/23 after worst-case interrupt |
| Automation-title filter as config regexes (NEW, v1.0) | [bot]-suffix misses human-named sync accounts | ✓ Good — corpus purged of 56 junk rows; generic knob, no hardcoded opinions |
| Frozen known-item eval as regression scale (NEW, v1.0) | Issue text is never embedded → leak-free label with precision 1.0 | ✓ Good — already caught that corpus 2× growth costs ranking nothing |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-12 after v1.0 milestone*
