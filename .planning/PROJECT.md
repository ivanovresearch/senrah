# Senrah

## What This Is

Senrah is an open-source Python tool that indexes the merged-PR history of a codebase and serves it to AI coding agents (Claude Code, Codex, and others) over MCP. When an agent works on a task, it can pull real precedents вЂ” how similar problems were actually solved in *this* codebase вЂ” instead of guessing. It is read-only retrieval over your own version-control history: ingest merged PRs, embed problem + solution, expose a versioned `search_prs_v1` MCP tool.

## Current State (v1.1 shipped 2026-06-18)

- Release-ready and public: GitHub Actions gates every push/PR (unit + integration on a pgvector testcontainer + gitleaks secret scan); reproducible sdist/wheel build with a tag-triggered TestPyPI publish via Trusted Publishing (no stored tokens); single-source version in `pyproject.toml`.
- First artifact live: `senrah 0.1.0` published to TestPyPI from tag `v0.1.0` and smoke-installed clean into a fresh venv (`senrah --version` -> 0.1.0).
- External-user docs complete: README install/quickstart (clone -> ingest -> index -> first search) + a copy-pasteable MCP-client config (Claude Code / Codex); CONTRIBUTING (dev setup, unit + Docker integration, gitleaks hook); CHANGELOG covering v1.0 + v1.1.
- `senrah init` no longer mangles an existing config: standalone comments and list indentation are preserved on re-run (FIX-01, regression-tested).
- Repo now lives under the `ivanovresearch` GitHub org; CI gitleaks runs the MIT CLI directly (org repos gate the gitleaks-action behind a paid license); actions pinned to Node 24 runtime versions.

### Prior State (v1.0 shipped 2026-06-12)

- Full pipeline live: ingest (GitHub, multi-repo, scoped, resumable) в†’ index (reindexable, model/version-tracked) в†’ search (CLI + MCP stdio/streamable-HTTP).
- Working corpus: dotnet/efcore (487 PRs, ~10 months) + encode/httpx (88 PRs), 575 skills rows, uniform `text-embedding-3-small/v2`.
- Retrieval quality instrumented: frozen known-item eval (`eval/knownitem/`, 218 queries вЂ” recall@1 0.670, recall@5 0.881, MRR@10 0.760) as the regression scale for any corpus/weights/model change.
- Agent-uplift A/B run (`eval/ab/`): 12 real tasks, control vs Senrah вЂ” statistical dead heat on outcomes, ZERO negative-uplift cases (the below-threshold confidence flag worked), two genuine convention-transfer wins from the deepest precedents available. Conclusion: **corpus depth, not weight tuning, is the next lever.**
- Test suite: 304 green (unit + integration incl. real-container MCP E2E); gitleaks pre-commit gate; QUAL-01..04 audited line-by-line (`docs/QUAL-AUDIT.md`).

## Core Value

An AI agent solving a task in this codebase can retrieve the most relevant real merged-PR precedents (problem + the diff that solved it) via MCP, ranked by semantic similarity. If everything else fails, that retrieval must work.

*(v1.0 check: still right. The A/B sharpened it вЂ” the value shows up as known-item retrieval ("this was fixed before") and convention transfer, both of which scale with corpus depth.)*

## Shipped Milestone: v1.1 Release Readiness (2026-06-18)

**Goal (met):** Make senrah ready for public use -- automated quality gates, a reproducible release pipeline, and documentation for external users. Pure engineering hardening; no new retrieval product. 11/11 requirements complete.

**Delivered:**
- CI pipeline (GitHub Actions): unit + integration (pgvector testcontainer) + gitleaks scan on push/PR -- a server-side gate independent of the per-clone hook.
- PyPI release pipeline: reproducible build, tag-triggered TestPyPI publish via Trusted Publishing, single-source versioning; `senrah 0.1.0` live on TestPyPI. Production upload stays a deliberate manual gate.
- User documentation: README install + MCP-client setup (Claude Code/Codex) + quickstart; CONTRIBUTING + CHANGELOG.
- `senrah init` comment-preservation fix (closed the accepted v1.0 debt).

## Current Milestone: v1.2 Corpus Depth — proving the lever

**Goal:** Determine whether, and by how much, multi-year corpus depth improves precedent retrieval — measured on a trustworthy temporal-holdout query-set, with a clean deduped eval scale built *first*. The v1.0 A/B named corpus depth (not weight tuning) the strongest product lever; this milestone tests that claim with the right instrument and ends on an explicit decision gate.

**Sequence (not alternatives):**

1. **Eval v3 (prerequisite).** Dedup backport clusters in the eval set and group them at corpus level so distractor counts stay honest as depth grows. Triage the 19 known-item misses (real retrieval failures vs duplicate/labeling artifacts). Outcome: a measuring stick trustworthy *before* the depth experiment runs — multi-year ingest multiplies backports, so an un-deduped deep corpus would make the delta uninterpretable.

2. **Corpus-depth experiment (main).** Full multi-year `dotnet/efcore` ingest (`--scope all`; rate-limit throttle out of scope — the 3.5-month ceiling was an assumption, not a blocker). A **depth ladder** — shallow (3.5mo baseline) → 1yr → 2–3yr — on **one** temporal-holdout query-set; T chosen from the deep end (2–3yr before T = deepest corpus, 1yr+ after T = query-set). We measure the *shape of the curve* (where hit-rate plateaus → recommended ingest depth), not a binary depth yes/no.

3. **Decision gate.** Hit-rate curve rises with depth → depth confirmed as the lever; plateau gives recommended ingest depth as a product setting. Curve flat on a clean deduped corpus → that's a result, and BM25/connectors rise in priority.

**Metric structure (the methodological core — layered, asymmetric gate):**
- **Primary / gate metric = automated temporal-holdout hit-rate** (known-item-style label, leak-free, reproducible). Coverage axis — *different* from known-item recall (ranking quality); measuring depth on known-item would falsely penalize it via added distractors.
- **LLM-judge = secondary layer** measuring the *unlinked* convention-transfer hits the automated label misses; the judge is itself calibrated to a small human gold set before it scores the ladder (an unvalidated judge is the exact failure Eval v3 exists to prevent). The judge lives in the `eval/` harness, **not** in senrah — "read-only search, no LLM providers in senrah" still holds.
- **Known-item recall@k = guardrail.** Depth must not erode ranking via distractors.
- **Gate synchronization:** "flat" means flat on the automated metric **AND** the judge layer shows no depth-curve in unlinked hits. A flat automated number alone does not close depth.

## Requirements

### Validated

- вњ“ Full GitHub-only MVP вЂ” v1.0 (31/31 requirements, archived in `milestones/v1.0-REQUIREMENTS.md`): storage schema + HNSW, connector seam + GitHub connector, scoped/incremental/resumable ingest with filters and rate-limit handling, dual-embedding indexing with `--reindex`, weighted thresholded search with multi-repo narrowing, versioned MCP tool over stdio+network, full CLI (`init`/`repos`/`ingest`/`index`/`search`/`serve`/`status`), opt-in search logging, secrets hygiene + gitleaks, test suite.
- v Release Readiness -- v1.1 (11/11 requirements, archived in `milestones/v1.1-REQUIREMENTS.md`): GitHub Actions CI (unit + pgvector-testcontainer integration + gitleaks) on push/PR; reproducible build + tag-to-TestPyPI publish via Trusted Publishing + single-source version (`senrah 0.1.0` live); README install/quickstart + MCP-client config + CONTRIBUTING + CHANGELOG; `senrah init` comment/indentation preservation (FIX-01).

### Active (v1.2 — being scoped into REQUIREMENTS.md)

- [ ] Eval v3: dedup backport clusters in the eval set + group at corpus level; triage the 19 known-item misses (real fails vs dup/labeling artifacts) — the trustworthy scale built *before* the depth experiment
- [ ] Corpus depth: full multi-year `dotnet/efcore` ingest (`--scope all`); depth ladder (3.5mo → 1yr → 2–3yr) on one temporal-holdout query-set; automated temporal-holdout hit-rate as primary/gate, LLM-judge secondary layer, known-item recall@k as guardrail

### Future (deferred past v1.2)

- [ ] Retrieval quality: BM25 hybrid, diff summary (A/B says second-order to corpus depth; priority rises only if the v1.2 gate shows depth flat)
- [ ] New connector (GitLab / Bitbucket / local git) via the existing seam (breadth = separate axis, after depth proven)

### Out of Scope

- **LLM providers (Claude/Codex/local) inside senrah** вЂ” senrah is read-only search; agents only *consume* MCP. *(v1.0 audit: still right вЂ” the A/B used external agents cleanly.)*
- **Abstraction over other vector databases** вЂ” pgvector chosen; repository-pattern seam only. *(Still right.)*
- **GitLab / Bitbucket connectors** вЂ” behind the connector seam. *(Still right; seam held вЂ” Ingester/MCP never import the concrete connector.)*
- **Diff chunking** вЂ” single-vector with truncation; raw diff stored so chunking is reindex-only later. *(Still right, with data: 19% of solution embeddings truncate at 6000 tokens вЂ” revisit only if eval shows misses concentrate in truncated targets.)*
- **LLM summarization of long descriptions** вЂ” head-priority truncation instead. *(Still right.)*
- **Re-checking edited merged-PR descriptions** вЂ” content_hash stored, no re-verify. *(Still right.)*
- **Cross-repo score normalization** вЂ” *(v1.0 data point: two-repo corpus showed zero eval interference; keep deferred.)*
- **Search access control** вЂ” repo tag is the future seam. *(Still right.)*
- **Production hardening (backups, HA)** вЂ” Docker Postgres acceptable. *(Still right.)*
- **Weight tuning as a project priority** вЂ” NEW (from A/B): re-ranking a shallow corpus cannot create uplift; tuning is second-order until corpus depth changes what there is to rank.

## Context

- Open-source, public repository вЂ” ENV-only secrets, `.env.example`, gitleaks gate, minimal token scopes.
- Three components with distinct lifecycles (Ingestion, Indexer, MCP server) + CLI control point; raw store between ingest and index so reindex never re-fetches. This held up: the v1в†’v2 embedding migration and the files_changed backfill both ran entirely from the raw store.
- Evidence culture established during v1.0: unit-green в‰  working (live testing found 3 ingest bugs, an MCP heartbeat scoping bug, and 2 init input holes that mocks missed); retrieval changes get measured on the frozen eval, not eyeballed.
- ~6.5k LOC Python (src), 304 tests. Key infra: psycopg3, pgvector HNSW, FastMCP, typer, ruamel (init write path), alembic (3 migrations).

## Constraints

- **Tech stack**: Python; PostgreSQL + pgvector (HNSW on both embedding columns) вЂ” chosen, not abstracted.
- **Embeddings**: OpenAI Text Embedding 3 Small, vector(1536); ~8191 token input limit drives truncation.
- **Security**: open-source code в†’ secrets only via environment variables; no real values anywhere in the repo; minimal token scopes documented.
- **Architecture**: connector interface is the core extensibility seam вЂ” a new source must require no changes to Indexer or MCP; MCP contract is versioned.
- **MCP server**: read-only, stateless over DB, starts independently, never touches the source at read-time.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| PostgreSQL + pgvector, no vector-DB abstraction | One chosen store; repository-pattern seam only | вњ“ Good вЂ” HNSW handled 575-row corpus at ~ms latency; zero store friction all milestone |
| Persist raw diff in DB | Code output, updates, reindex without re-fetch | вњ“ Good вЂ” enabled `--reindex`, files_changed backfill, and the A/B reference diffs, all with zero GitHub calls |
| solution_embedding = clean diff | Template metadata is noise for semantic search | вњ“ Good вЂ” known-item recall@5 0.881 on problem+solution composite |
| Head-priority truncation, no LLM summarization | Avoids extra call, cost, failure point | вњ“ Good вЂ” 19% of diffs truncate; eval shows no miss concentration there yet |
| Top-N with threshold | Don't dump irrelevant tail | вњ“ Good вЂ” in the A/B the [BELOW THRESHOLD] flag produced ZERO misled-agent cases |
| Configurable weights (0.6/0.4) | Tunable per project | вљ  Revisit framing вЂ” A/B showed tuning is second-order to corpus depth; keep configurable, don't invest |
| Versioned MCP tool name | Format changes don't break dependent prompts | вњ“ Good (untested by an actual v2 yet) |
| ENV-only secrets | Open-source posture | вњ“ Good вЂ” gitleaks history scan clean across all 70+ commits |
| No LLM providers in senrah | Read-only search | вњ“ Good |
| Incremental ingest by `merged_at` cursor | Simplest correct cursor | вњ— REVERSED (BUG C) вЂ” high-water cursor as a resume boundary lost ~50% of a window on interrupt; replaced by full-scope re-scan + present-in-DB probe; cursor demoted to diagnostic. Lesson: a documented-but-under-scoped edge is still a blocker |
| Build from spec, not prototype | Spec is source of truth | вњ“ Good |
| Full-scope re-scan + probe owns resume correctness (NEW, v1.0) | Bounded scopes make re-scan cheap; probe makes it free; recovers errored PRs without extra machinery | вњ“ Good вЂ” live-proven 23/23 after worst-case interrupt |
| Automation-title filter as config regexes (NEW, v1.0) | [bot]-suffix misses human-named sync accounts | вњ“ Good вЂ” corpus purged of 56 junk rows; generic knob, no hardcoded opinions |
| Frozen known-item eval as regression scale (NEW, v1.0) | Issue text is never embedded в†’ leak-free label with precision 1.0 | вњ“ Good вЂ” already caught that corpus 2Г— growth costs ranking nothing |
| TestPyPI publish via Trusted Publishing, not stored tokens (NEW, v1.1) | OIDC-scoped to the publish job + protected environment; no long-lived secret in the repo or CI | v Good -- live publish of 0.1.0 worked end-to-end, zero stored credentials |
| Single-source version in pyproject.toml, tag must match (NEW, v1.1) | One place to bump; release tag derives the artifact version | v Good -- `senrah 0.1.0` from tag `v0.1.0` |
| gitleaks CLI in CI, not gitleaks-action (NEW, v1.1) | gitleaks-action requires a paid GITLEAKS_LICENSE on org-owned repos; the MIT CLI does not | v Good -- mirrors the local pre-commit hook; no license, no secret |
| Production PyPI upload stays a manual gate (v1.1) | Pipeline + TestPyPI proves the mechanism; real publish is a deliberate human decision | -- Pending -- carried to a future milestone |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? в†’ Move to Out of Scope with reason
2. Requirements validated? в†’ Move to Validated with phase reference
3. New requirements emerged? в†’ Add to Active
4. Decisions to log? в†’ Add to Key Decisions
5. "What This Is" still accurate? в†’ Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check вЂ” still the right priority?
3. Audit Out of Scope вЂ” reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-22 — milestone v1.2 Corpus Depth scoped via /gsd-new-milestone*
