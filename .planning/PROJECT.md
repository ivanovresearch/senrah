# Harness

## What This Is

Harness is an open-source Python tool that indexes the merged-PR history of a codebase and serves it to AI coding agents (Claude Code, Codex, and others) over MCP. When an agent works on a task, it can pull real precedents — how similar problems were actually solved in *this* codebase — instead of guessing. It is read-only retrieval over your own version-control history: ingest merged PRs, embed problem + solution, expose a `search_prs` MCP tool.

## Core Value

An AI agent solving a task in this codebase can retrieve the most relevant real merged-PR precedents (problem + the diff that solved it) via MCP, ranked by semantic similarity. If everything else fails, that retrieval must work.

## Requirements

### Validated

(None yet — ship to validate)

### Active

<!-- Full GitHub-only MVP per the technical spec. -->

**Storage & data model**
- [ ] PostgreSQL + pgvector schema: `projects`, `repositories`, `pull_requests` (raw store incl. diff + content_hash), `skills` (problem_embedding + solution_embedding, embedding_model, embedding_version)
- [ ] Repository-pattern data-access boundary (no abstraction over other vector DBs, just a clean seam)
- [ ] Raw `diff` persisted in DB — enables code output, updates, and reindex without re-fetching from source

**Ingestion (GitHub MVP)**
- [ ] Extensible connector interface: `validate_credentials`, `list_merged_prs(filters, cursor)`, `fetch_pr(number)`, `rate_limit_status`
- [ ] GitHub connector implementation behind that interface
- [ ] Load filters: merged-only, non-empty diff, exclude bots (`[bot]` suffix + configurable stop-list), exclude giant PRs (>100 files OR >5000 lines, configurable)
- [ ] Per-repo ingest scope: `all` / `last_n` / `since_date` / `period` (by `merged_at`)
- [ ] Incremental ingest by `merged_at` cursor only (store content_hash but do not re-check edited descriptions in MVP)
- [ ] Rate limiting + resumability: configurable `RATE_LIMIT`, respect `rate_limit_status`, backoff, resume from cursor on interruption, per-PR errors logged without aborting the run

**Indexing & embeddings**
- [ ] OpenAI Text Embedding 3 Small for both embeddings
- [ ] `problem_embedding` = `title` + issue/description body; truncate (head-priority: title + first paragraph) when over ~8191 tokens, no LLM summarization
- [ ] `solution_embedding` = clean diff truncated to `EMBED_DIFF_LIMIT` (no factual_summary template — metadata stays in DB for filtering/display only)
- [ ] Reindex from raw store: `harness index --reindex` using `embedding_model` + `embedding_version`, no source round-trip
- [ ] Diff chunking deliberately deferred but unblocked (raw diff stored → reindex-only later)

**Search & scoring**
- [ ] Score = `W_PROBLEM × problem_sim + W_SOLUTION × solution_sim`, weights configurable (default 0.6 / 0.4)
- [ ] Top-N with threshold: default N=5 (configurable), drop results below `SCORE_THRESHOLD`
- [ ] Multi-repo search across all repos in a project by default; optional `repo`/`repos[]` narrowing; each result tagged with source repo

**MCP server**
- [ ] Versioned `search_prs_v1` tool (contract version in the name)
- [ ] Input: `query` (required), `repos[]` (opt), `limit` (opt), `debug` (opt)
- [ ] Output per result: PR number/title, score (p/s components only when `debug=true`), repo, author, merged_at, linked issue (if any), files (max 6 + "+K more"), PR link, and a diff excerpt truncated to `OUTPUT_DIFF_LIMIT`
- [ ] Read-only, stateless over the DB; starts independently of ingest/index; never hits the source at read-time
- [ ] Transports: stdio (local agent) and network (shared team instance)

**CLI**
- [ ] `init` (create project, add repos, enter + validate credential scopes)
- [ ] `repos` (list connected repos, status, scope)
- [ ] `ingest` (run/resume per-repo load with progress)
- [ ] `index` (build embeddings from raw store; `--reindex`)
- [ ] `status` (ingest, index, MCP server observability — see below)
- [ ] `serve` (start MCP server, stdio or network)
- [ ] `search "<text>"` (terminal-only retrieval test, no MCP — quality debugging)

**Observability & config**
- [ ] `status` reports: per-repo ingest counts + cursor + rate-limit remaining/reset + errored PRs; index vector count + model/version + raw PRs lacking embeddings + last index time; MCP up/down + transport + request count + latency
- [ ] Optional search logging (`[E4]`, off by default, configurable, with privacy note for private repos)
- [ ] Secrets read **only** from environment variables; no coupling to any secret backend
- [ ] Public-repo hygiene: `.env.example` with placeholders only, `.env` git-ignored, README documents minimal token scopes (GitHub read-only PR/issues, OpenAI embeddings-only)

**Quality**
- [ ] Thorough testing: unit tests on scoring/truncation/filtering, connector tests with mocked API, E2E on a real test repository

### Out of Scope

- **LLM providers (Claude/Codex/local) inside harness** — harness is read-only search; agents only *consume* MCP, they are not part of harness. Embeddings are done by a dedicated embedding model.
- **Abstraction over other vector databases** — only a clean repository-pattern data-access seam; pgvector is the chosen store.
- **GitLab / Bitbucket connectors** — deferred behind the connector interface; GitHub only in MVP.
- **Diff chunking** — single-vector with truncation in MVP; raw diff stored so chunking is reindex-only later.
- **LLM summarization of long descriptions** — head-priority truncation instead (avoids extra call, cost, failure point).
- **Re-checking edited merged-PR descriptions** — content_hash stored, but no re-verification in MVP.
- **Cross-repo score normalization** — known MVP limitation; top may skew toward a "loud" repo.
- **Search access control / per-permission filtering** — deferred (repo tag on each PR is the future seam).
- **Production hardening (backups, HA)** — Dockerized Postgres+pgvector acceptable for MVP.
- **`commit_messages` in MCP output** — stored in DB, not returned.

## Context

- **Greenfield build, spec-driven.** A prototype exists but its code is NOT a reference — this technical spec is the single source of truth. The spec's §12 "differences from the prototype" list is therefore the set of decisions we implement directly (diff persisted, clean-diff solution embedding, truncation, diff excerpt in output, top-N+threshold, debug-only score components, configurable weights/limits, versioned MCP contract, ENV-only secrets, no LLM providers).
- **Three components with distinct lifecycles** (Ingestion, Indexer, MCP server) plus a CLI as the control point, with a raw store (`pull_requests` table) between ingestion and indexing so reindex never re-fetches.
- **Open-source, public repository** — drives the ENV-only secrets posture and `.env.example`/scope-documentation hygiene.
- **Deployment models:** solo developer (local stdio MCP, local/Docker Postgres) and shared team instance (network MCP, shared Postgres, ENV secrets from any backend; read-only + stateless → scales simply).

## Constraints

- **Tech stack**: Python; PostgreSQL + pgvector (ivfflat/hnsw indexes on both embedding columns) — chosen, not abstracted.
- **Embeddings**: OpenAI Text Embedding 3 Small, vector(1536); ~8191 token input limit drives truncation.
- **Security**: open-source code → secrets only via environment variables; no real values anywhere in the repo (configs or tests); minimal token scopes documented.
- **Architecture**: connector interface is the core extensibility seam — a new source must require no changes to Indexer or MCP; MCP contract is versioned so output-format changes don't break dependent agents/prompts.
- **MCP server**: read-only, stateless over DB, must start independently and never touch the source at read-time.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| PostgreSQL + pgvector, no vector-DB abstraction | One chosen store; only a repository-pattern access seam needed | — Pending |
| Persist raw diff in DB | Enables code output, updates, reindex without re-fetch | — Pending |
| solution_embedding = clean diff (drop factual_summary) | Template metadata is noise for semantic search; keep it in DB for filtering/display | — Pending |
| Head-priority truncation, no LLM summarization | Avoids extra call, cost, and a failure point | — Pending |
| Top-N with threshold (N=5 default) | Don't dump irrelevant tail on narrow queries | — Pending |
| Configurable scoring weights (0.6/0.4) + limits | Tunable per project | — Pending |
| Versioned MCP tool name (`search_prs_v1`) | Format changes don't break dependent prompts/agents | — Pending |
| ENV-only secrets, backend user's choice | Open-source; not coupled to any secret manager | — Pending |
| No LLM providers in harness | Read-only search; agents consume MCP, aren't part of harness | — Pending |
| Incremental ingest by `merged_at` only | Simplest correct cursor; edited-PR re-check deferred | — Pending |
| Build from spec, not prototype code | Spec is source of truth; §12 lists deliberate divergences | — Pending |

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
*Last updated: 2026-05-30 after initialization*
