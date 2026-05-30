<!-- GSD:project-start source:PROJECT.md -->
## Project

**Harness**

Harness is an open-source Python tool that indexes the merged-PR history of a codebase and serves it to AI coding agents (Claude Code, Codex, and others) over MCP. When an agent works on a task, it can pull real precedents — how similar problems were actually solved in *this* codebase — instead of guessing. It is read-only retrieval over your own version-control history: ingest merged PRs, embed problem + solution, expose a `search_prs` MCP tool.

**Core Value:** An AI agent solving a task in this codebase can retrieve the most relevant real merged-PR precedents (problem + the diff that solved it) via MCP, ranked by semantic similarity. If everything else fails, that retrieval must work.

### Constraints

- **Tech stack**: Python; PostgreSQL + pgvector (ivfflat/hnsw indexes on both embedding columns) — chosen, not abstracted.
- **Embeddings**: OpenAI Text Embedding 3 Small, vector(1536); ~8191 token input limit drives truncation.
- **Security**: open-source code → secrets only via environment variables; no real values anywhere in the repo (configs or tests); minimal token scopes documented.
- **Architecture**: connector interface is the core extensibility seam — a new source must require no changes to Indexer or MCP; MCP contract is versioned so output-format changes don't break dependent agents/prompts.
- **MCP server**: read-only, stateless over DB, must start independently and never touch the source at read-time.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Recommended Stack
### Core Technologies
| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.12+ | Runtime | 3.10 is the floor for every dependency in this stack; 3.12 is the stable current release with measurable async perf gains and `asyncio` improvements |
| `mcp` (Model Context Protocol Python SDK) | 1.27.2 | MCP server — stdio + streamable-HTTP transports, tool registration | Official Anthropic SDK; FastMCP high-level API covers 95% of tool-server needs in ~10 lines; `stateless_http=True` for network deploy |
| `psycopg[binary,pool]` | 3.3.4 | PostgreSQL driver — sync + async, native COPY, connection pool | Psycopg 3 is the active successor to psycopg2; native async (`AsyncConnection`, `AsyncConnectionPool`) required for the MCP server's async context; the `[binary]` extra uses libpq C bindings for speed; `[pool]` gives `ConnectionPool` / `AsyncConnectionPool` without an extra dep |
| `pgvector` | 0.4.2 | pgvector Python integration — register vector type, distance operators | Official pgvector project; `register_vector(conn)` wires the `vector` type into psycopg3; exposes `<=>` (cosine), `<->` (L2), `<#>` (inner product) operators through psycopg |
| `openai` | 2.38.0 | OpenAI embeddings API client | Official SDK; v2 has `AsyncOpenAI` client, batch input support (array of strings), and typed `Embedding` response objects |
| `PyGithub` | 2.9.1 | GitHub REST API v3 — merged PRs, files, rate-limit status | Maintained, typed wrappers for every endpoint; built-in `GithubRetry` for primary+secondary rate limits; use `repo.get_pulls(state="closed")` then filter `merged_at is not None`; diff fetched via `pull.diff_url` + `requester._Requester__requestEncode` or raw `httpx` with token (see note below) |
| `httpx` | 0.28.1 | HTTP client for raw GitHub diff fetch | PyGithub exposes `diff_url` but no direct method to retrieve content; `httpx.get(pull.diff_url, headers={"Accept": "application/vnd.github.v3.diff", "Authorization": f"token {token}"})` is the idiomatic approach; `httpx.AsyncClient` for async ingestion pipeline |
| `typer` | 0.26.3 | CLI framework | Built on Click; type-hint-driven — no boilerplate for options and arguments; `typer.Typer(invoke_without_command=True)` for sub-command dispatch; ships rich progress/help output out of the box |
| `pydantic-settings` | 2.14.1 | ENV-only config + secrets management | `BaseSettings` reads all secrets from environment variables, validates types, and provides `.env` file loading for local dev; aligns with the project's ENV-only secrets posture; no coupling to any cloud secret backend |
| `alembic` | 1.18.4 | Database schema migrations | Tracks migration history; works in raw-SQL mode via `op.execute()` — no ORM models needed (see patterns section); required for `CREATE EXTENSION vector`, schema creation, and future index changes |
| `tenacity` | 9.1.4 | Retry/backoff for GitHub rate limits and OpenAI transient errors | `@retry(wait=wait_exponential(...), stop=stop_after_attempt(n), retry=retry_if_exception_type(...))` is the cleanest retry pattern; works async; avoids hand-rolling sleep loops |
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pytest` | 9.0.3 | Test runner | All tests |
| `pytest-asyncio` | 1.4.0 | Async test functions | Any test that `await`s — ingestion pipeline, MCP handler, async DB ops |
| `respx` | 0.23.1 | Mock `httpx` requests in tests | Unit tests for GitHub connector — mock the diff URL fetch, paginated PR list, rate limit responses |
| `testcontainers[postgres]` | 4.14.2 | Real PostgreSQL container in integration tests | Use `PostgresContainer("pgvector/pgvector:pg17")` image to get pgvector pre-installed; replaces brittle mock-DB approaches for repository-layer tests |
| `python-dotenv` | (latest) | Load `.env` for local dev | `pydantic-settings` already calls `dotenv` under the hood when configured with `env_file = ".env"`; include `python-dotenv` explicitly only if you need standalone `load_dotenv()` calls |
### Development Tools
| Tool | Purpose | Notes |
|------|---------|-------|
| `uv` | Fast package manager + virtual env | `uv pip install`, `uv run`; replaces pip+venv; `uv add "mcp[cli]"` is the official MCP install recommendation |
| `ruff` | Linting + formatting | Replaces flake8 + black + isort; single tool, fastest linter available |
| `mypy` | Static type checking | Required — the repository pattern, connector interface, and MCP output types benefit significantly from static types |
| `pyproject.toml` | Project metadata + deps | PEP 517/518 standard; `[project.scripts]` entry point for the `harness` CLI |
## Installation
# Core runtime
# Dev / test
## Alternatives Considered
| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| MCP transport | FastMCP (`mcp` SDK 1.27) | Rolling your own JSON-RPC over stdio | FastMCP handles protocol negotiation, capability advertisement, and error marshaling; no value in re-implementing the spec |
| PostgreSQL driver | psycopg3 | asyncpg | asyncpg is async-only; psycopg3 supports both sync (CLI/migration code) and async (MCP server) in one package; pgvector-python has first-class psycopg3 support; asyncpg requires a separate sync driver |
| PostgreSQL driver | psycopg3 | SQLAlchemy Core/ORM | This project uses repository-pattern data access — raw SQL is more legible and direct for vector similarity queries; SQLAlchemy adds ~300 KB of dependency weight and abstracts over the `<=>` cosine operator awkwardly |
| pgvector index | HNSW (`vector_cosine_ops`) | IVFFlat | HNSW: ~1.5 ms search vs IVFFlat ~2.4 ms at 99% recall; no rebuild required on incremental ingest (critical for harness's continuous ingest pattern); higher memory usage acceptable at MVP scale (<100K PRs) |
| GitHub API client | PyGithub + httpx | gidgethub | gidgethub is sans-I/O and requires choosing an async HTTP backend; PyGithub has built-in `GithubRetry`, typed objects, and a larger community; diff fetching still requires a raw HTTP call regardless of choice |
| GitHub API client | PyGithub + httpx | Raw httpx only | PyGithub saves writing pagination, object deserialization, and retry logic for all PR/issue metadata endpoints; only the diff endpoint needs raw httpx (media type header) |
| CLI framework | typer | click | Typer is built on Click but eliminates argument/option decorator boilerplate via type hints; same power, less code |
| CLI framework | typer | argparse | argparse has no sub-command dispatch elegance; no rich help text; avoid for a multi-command CLI |
| Config/secrets | pydantic-settings | python-dotenv alone | pydantic-settings provides validation (e.g., `EMBED_BATCH_SIZE: int = 100`), type coercion, and the same `.env` loading; no reason to use bare `dotenv` |
| Migrations | alembic (raw SQL mode) | sqitch / Flyway | Both would work but add non-Python tooling; alembic ships with pip, uses `op.execute()` for raw SQL, and generates revision files with upgrade/downgrade stubs — sufficient for this schema |
| Retry | tenacity | manual `time.sleep` loops | tenacity handles jitter, exponential backoff, async support, and per-exception filtering; `time.sleep` in a loop is fragile and untestable |
| Test DB fixture | testcontainers | pytest-postgresql | pytest-postgresql v8 requires a local PostgreSQL binary and doesn't ship pgvector; testcontainers pulls `pgvector/pgvector:pg17` from Docker Hub — no local install needed, CI-friendly |
## What NOT to Use
| Avoid | Why | Use Instead |
|-------|-----|-------------|
| SQLAlchemy ORM | Adds a full ORM layer with no benefit over raw SQL for a repository-pattern design; `Column(Vector(1536))` adds mapping complexity without improving the cosine-distance queries | Raw psycopg3 SQL + alembic migrations |
| LangChain / LlamaIndex | Harness is purpose-built read-only search; these frameworks add multi-LLM abstraction, chain orchestration, and document loaders that are all out of scope; they also have heavy transitive dependencies and fast-breaking APIs | Direct `openai` SDK + custom chunking/truncation |
| SSE transport for MCP | Deprecated in favor of Streamable HTTP; MCP SDK still supports it but it's the legacy path | `mcp.run(transport="streamable-http")` |
| psycopg2 | Maintenance mode; no native async; not recommended for new projects by the psycopg maintainers themselves | psycopg3 (`psycopg[binary,pool]`) |
| asyncpg | Async-only — ingestion CLI code and migration scripts are sync; would require a second driver | psycopg3 (supports both sync and async) |
| `requests` library | Sync-only; httpx provides the same API plus async; respx only mocks httpx | httpx |
| VCR.py / pytest-recording | VCR cassettes encode real API secrets in fixture files — a security risk for an open-source repo with GitHub tokens | respx (code-level mocks, no recorded files, no secret leakage) |
| python-dotenv standalone | Already included transitively via pydantic-settings; calling `load_dotenv()` directly bypasses pydantic's type validation | pydantic-settings `BaseSettings` with `env_file` |
## Stack Patterns by Variant
## pgvector Index Tradeoffs
| Criterion | HNSW | IVFFlat |
|-----------|------|---------|
| Query latency (99% recall) | ~1.5 ms | ~2.4 ms |
| Recall at default params | 95%+ | 90-95% |
| Index build time | Slower | Faster |
| Memory usage | 2–5× IVFFlat | Lower |
| Incremental inserts | No rebuild needed | Rebuild or degrade |
| Tuning complexity | Low (`m`, `ef_construction`) | Medium (`lists`, `probes`) |
## Version Compatibility
| Package | Requires | Notes |
|---------|----------|-------|
| `psycopg` 3.3.4 | Python ≥3.10 | `[binary]` extra requires libpq; use `[pure]` in environments without libpq |
| `mcp` 1.27.2 | Python ≥3.10 | v2 branch exists but is pre-alpha; use v1 stable |
| `pydantic-settings` 2.14.1 | Python ≥3.10, pydantic v2 | Do not mix with pydantic v1 |
| `testcontainers[postgres]` 4.14.2 | Python ≥3.10, Docker daemon | CI must have Docker available; use `pgvector/pgvector:pg17` image |
| `alembic` 1.18.4 | SQLAlchemy ≥2.0 (used internally by alembic even with raw SQL) | SQLAlchemy is a dep of alembic but not used in application code |
| `PyGithub` 2.9.1 | Python ≥3.9 | Use `GithubRetry` constructor arg on `Github()` init |
| `openai` 2.38.0 | Python ≥3.8 | Use `AsyncOpenAI` for the async indexing pipeline |
## Sources
- PyPI `mcp` package page — version 1.27.2 confirmed (2026-05-29)
- [MCP Python SDK GitHub README](https://github.com/modelcontextprotocol/python-sdk) — FastMCP API, stdio/streamable-http transport patterns, `stateless_http=True`
- [FastMCP deployment docs](https://gofastmcp.com/deployment/running-server) — `transport="http"`, host/port params, `mcp.http_app()` for ASGI mount
- PyPI `pgvector` page — version 0.4.2 (2025-12-05)
- [pgvector GitHub README](https://github.com/pgvector/pgvector) — HNSW/IVFFlat index syntax, pgvector extension version 0.8.2
- [pgvector-python GitHub README](https://github.com/pgvector/pgvector-python) — `register_vector`, psycopg3 async pattern
- PyPI `psycopg` page — version 3.3.4 (2026-05-01), Python ≥3.10
- PyPI `openai` page — version 2.38.0 (2026-05-21)
- [OpenAI embeddings guide](https://developers.openai.com/api/docs/guides/embeddings) — text-embedding-3-small: 1536 dims, 8192 token limit, batch input array, `AsyncOpenAI`
- PyPI `PyGithub` page — version 2.9.1 (2026-04-14)
- [GitHub PR diff media type](https://github.com/orgs/community/discussions/24460) — `Accept: application/vnd.github.v3.diff` header pattern
- PyPI `typer` page — version 0.26.3 (2026-05-28)
- PyPI `pydantic-settings` page — version 2.14.1 (2026-05-08)
- PyPI `alembic` page — version 1.18.4 (2026-02-10); `op.execute()` raw SQL pattern confirmed in docs
- PyPI `tenacity` page — version 9.1.4 (2026-02-07)
- PyPI `respx` page — version 0.23.1 (2026-04-08); httpx mock fixture pattern
- PyPI `testcontainers` page — version 4.14.2 (2026-03-18); `pgvector/pgvector:pgXX` image confirmed
- PyPI `pytest-asyncio` page — version 1.4.0 (2026-05-26)
- PyPI `pytest` page — version 9.0.3 (2026-04-07)
- PyPI `httpx` page — version 0.28.1 (2024-12-06)
- HNSW vs IVFFlat: DEV Community, Medium, AWS blog — HNSW recommended for incremental-ingest workloads (MEDIUM confidence — community sources, consistent across multiple)
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
