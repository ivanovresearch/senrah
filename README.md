# Senrah

[![CI](https://github.com/ivanovresearch/senrah/actions/workflows/ci.yml/badge.svg)](https://github.com/ivanovresearch/senrah/actions/workflows/ci.yml)

**Precedent retrieval for AI coding agents.** Senrah indexes the merged-PR
history of a codebase — the problem and the diff that actually solved it — into
your own Postgres+pgvector, and serves it to any MCP-capable agent (Claude
Code, Codex, Cursor). When an agent works on a task, it retrieves real
precedents — how similar problems were actually solved in *this* codebase —
instead of guessing.

**Why PR history and not just code?** Every mainstream "codebase context" tool
embeds what your code *is*. Senrah embeds how your team has historically
*changed* it. A merged PR is a problem→solution pair: the issue or description
states what was wrong, and the diff shows the fix your team actually accepted.
That trajectory encodes the conventions, workarounds, and edge-case lore that
base models have never seen — which matters most in large and legacy codebases,
where "the way we do it here" is nowhere in the current source and everywhere
in the history.

Senrah is read-only retrieval over your own version-control history: ingest
merged PRs, embed problem + solution, expose a `search_prs_v1` MCP tool. It
never writes to your repos and never touches the source at read time.

**Project status:** milestone v1.1 — GitHub-only MVP, validated end-to-end on
a single corpus (`dotnet/efcore`). See [Roadmap](#roadmap) for what is deliberately not
done yet.

## How it works

```mermaid
flowchart LR
    subgraph Ingest
        GH[GitHub API] -->|merged PRs + diffs| C[Connector<br/>ConnectorProtocol]
        C --> I[Ingester<br/>resumable, filtered]
    end
    subgraph Store
        I -->|raw problem + diff| DB[(PostgreSQL<br/>+ pgvector)]
        E[Indexer / Embedder<br/>token-aware truncation] -->|dual embeddings<br/>HNSW indexes| DB
        DB --> E
    end
    subgraph Serve
        DB -->|read-only| M[MCP server<br/>search_prs_v1]
        M -->|stdio / streamable-HTTP| A[AI coding agent]
    end
```

Three decoupled stages:

1. **Ingest** — a connector pulls merged PRs (problem text + diff) from the
   source. The connector interface is a `typing.Protocol` with an enforced
   import boundary: connectors never import the indexer, ingester, or DB
   layers, and the ingester only ever sees the protocol. Adding a new source
   (GitLab, Bitbucket, internal Git hosting) requires zero changes to the
   Indexer or the MCP server. Ingest is resumable and idempotent: every run
   re-scans the configured scope and a cheap present-in-DB probe skips
   already-ingested PRs, so an interrupted run is deterministically
   back-filled on the next one. Each PR commits in its own transaction;
   per-PR errors are isolated, logged, and surfaced by `senrah status`.
2. **Index** — problem and solution are embedded separately
   (`text-embedding-3-small`, 1536 dims), truncated by model tokens (never
   characters, titles survive), and stored with the embedding model + version
   per row. HNSW (`vector_cosine_ops`) indexes on both columns; no rebuild
   needed on incremental ingest. `senrah index --reindex` rebuilds all vectors
   from the raw store — an embedding-model migration path that makes no GitHub
   calls.
3. **Serve** — a stateless, read-only MCP server exposes one versioned tool,
   `search_prs_v1`. ANN oversampling plus a weighted problem/solution
   composite re-rank; results carry qualitative confidence labels (score bands
   tuned to the embedding model's practical score range), and the
   no-match case returns a near-miss envelope instead of an empty list.

## Quick Start

### Prerequisites

- Python 3.12+
- Docker (for local Postgres+pgvector)

### Setup

1. **Clone the repository:**

   ```bash
   git clone https://github.com/ivanovresearch/senrah.git
   cd senrah
   ```

2. **Start the database:**

   ```bash
   docker compose up -d
   ```

3. **Install senrah:**

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   ```

4. **Configure secrets** (copy `.env.example` → `.env`, fill in real values — never commit `.env`):

   ```bash
   cp .env.example .env
   # Edit .env with your real DATABASE_URL, GITHUB_TOKEN, OPENAI_API_KEY
   ```

5. **Configure your project** (copy `senrah.yaml.example` → `senrah.yaml`):

   ```bash
   cp senrah.yaml.example senrah.yaml
   # Edit senrah.yaml to point at your repos (no secrets here)
   ```

6. **Run migrations.** Note: Alembic reads `DATABASE_URL` from the real
   environment, not from `.env` (only `senrah` commands load `.env`), so
   export it first:

   ```bash
   export DATABASE_URL="postgresql://harness:harness@localhost:5432/harness"   # matches the bundled docker-compose.yml; use your own credentials in production
   alembic upgrade head
   ```

   On Windows (PowerShell): `$env:DATABASE_URL = "postgresql://..."` then
   `alembic upgrade head`.

7. **Ingest, index, and search:**

   ```bash
   senrah ingest
   senrah index
   senrah search "fix for cursor pagination in async resolver"
   ```

### CLI overview

| Command | Purpose |
|---------|---------|
| `senrah init` | Interactive bootstrap: validates credentials live, writes/updates `senrah.yaml` (comment-preserving) |
| `senrah ingest` | Fetch merged PRs into the raw store; `--scope last_n 200 \| period 90d \| since_date 2024-01-01 \| all -` overrides config |
| `senrah index` | Embed un-indexed PRs; `--reindex` rebuilds all vectors (embedding-model migration, no GitHub calls) |
| `senrah search` | Query from the command line (same scoring as the MCP tool) |
| `senrah serve` | Start the MCP server (stdio default, `--transport network` for streamable-HTTP) |
| `senrah repos` | Show per-repo ingest state |
| `senrah status` | Health view: PR counts, last-run errors, GitHub rate limit, index coverage, MCP heartbeat + latency |

## Use with an AI agent (MCP)

Senrah serves your indexed PR history to an AI coding agent over the Model
Context Protocol. `senrah serve` defaults to **stdio** transport, so the agent
launches it as a subprocess. The server exposes a single read-only tool,
`search_prs_v1`; it queries the database only and never contacts GitHub at read
time.

Add senrah to your MCP client config (e.g. Claude Code / Codex). The `env`
values below are **placeholders** — substitute your own and never commit real
secrets:

```json
{
  "mcpServers": {
    "senrah": {
      "command": "senrah",
      "args": ["serve"],
      "env": {
        "DATABASE_URL": "postgresql://USER:PASSWORD@HOST:5432/DB",
        "GITHUB_TOKEN": "github_pat_...",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

`OPENAI_API_KEY` is required because the server embeds the incoming query.
`GITHUB_TOKEN` must also be set: the server itself never contacts GitHub at
read time (no connector code is even imported on the serve path), but startup
configuration validation currently requires all three variables to be present,
so `senrah serve` refuses to start without it. Point `DATABASE_URL` at the
same database you ingested and indexed into.

For a remote setup, run `senrah serve --transport network` instead — a
streamable-HTTP server that binds `127.0.0.1` by default (use `--host 0.0.0.0`
only when you intentionally expose it to a shared network).

## Retrieval quality

Known-item retrieval (given an issue whose fixing PR is known, does that PR
rank in the top-k?) on a deduped `dotnet/efcore` corpus — 575 merged PRs
(2024-04-06 … 2026-06-12), 218 held-out queries, backport clusters scored
per-cluster so cherry-picks cannot inflate recall. The eval protocol uses
problem/solution weights 0.7/0.3 (the shipped default is 0.6/0.4). The
manifest is hash-pinned and the run is deterministic.

| Metric | Value |
|--------|-------|
| recall@1 | 0.71 |
| recall@5 | 0.90 |
| recall@10 | 0.93 |
| MRR@10 | 0.79 |

That number measures **ranking quality**, not forward coverage. The harder
question — for a genuinely new task, does a relevant precedent that predates it
exist and get retrieved, with no leakage from the future? — is measured by a
separate leak-free temporal-holdout harness (corpus strictly before a cutoff T,
queries strictly after, split frozen on `merged_at` and original ingest
timestamps, relevance human-anchored via TREC-style pooling on a blind gold
set). That instrument exists and its leakage assumptions are checked, but the
full-power forward-coverage number is not yet reported: the LLM judge intended
to scale the labeling failed its pre-registered calibration gate (κ = 0.39 vs a
0.6 floor) and was demoted to advisory-only, so scaling is currently
human-labeling-bound. The full account, including the negative results, is in
[docs/EVAL.md](docs/EVAL.md).

## Built for enterprise / legacy codebases

- **Self-hosted, your database.** Runs against your own PostgreSQL+pgvector.
  Your PR history stays in your infrastructure — with one precisely-stated
  exception: embedding is an external API call. Problem text and truncated
  diffs are sent to the configured embeddings endpoint (OpenAI by default).
  The endpoint is pluggable via `embed.base_url` (any OpenAI-compatible
  provider, including a local one returning 1536-dim vectors), and the
  connector interface is likewise a seam for internal Git hosting.
- **Read-only by construction.** Ingest needs only read scopes (see
  [token scopes](#required-token-scopes)); the MCP server is stateless over
  the database and never touches the source or GitHub at read time.
- **Data-loss correctness, live-validated.** The resume model was validated
  against a real interrupted-and-resumed ingest: a worst-case kill-and-resume
  produced a PR set identical to an uninterrupted reference run (an
  earlier cursor-semantics bug that silently lost 13/27 PRs was root-caused
  and fixed, with regression tests that were red before the fix). See
  [docs/PRODUCTION-READINESS.md](docs/PRODUCTION-READINESS.md).
- **Secrets posture.** Secrets come only from environment variables; any
  secret-shaped key in `senrah.yaml` is a startup error; a fail-closed
  gitleaks pre-commit hook (missing scanner blocks the commit) plus a
  full-history gitleaks CI job plus automated hygiene tests.
- **Stable agent-facing contract.** The MCP tool is versioned
  (`search_prs_v1`) with a typed output schema, so output-format changes
  cannot silently break dependent agents or prompts.
- **Ops introspection without extra infrastructure.** `senrah status` reports
  per-repo state, persisted per-PR ingest errors, live GitHub rate limit,
  embedding-model coverage, and MCP server heartbeat with p50/p90 latency —
  all from the DB and a small metrics file.
- **Supply-chain-conscious releases.** CI on every PR and push to main (unit +
  real-pgvector integration + gitleaks); tag-triggered release with OIDC
  Trusted Publishing — no stored PyPI token anywhere.

## Where senrah fits

Honest positioning, not a feature matrix:

- **Code-context tools** (Sourcegraph Cody, Cursor indexing, Continue, Aider's
  repo-map, semantic code search) embed the *current* code. Senrah is
  complementary, not a replacement: it embeds the history of accepted changes.
- **PR-history review tools** (Qodo, CodeRabbit, Greptile) learn from past PRs
  to comment on *reviews*. Senrah sits on the write path instead: it feeds
  precedents to the agent while it is solving the task, and it is
  agent-agnostic over MCP rather than tied to one vendor's bot.
- **Where it earns its keep**: brownfield and legacy repos with strong
  house conventions. Retrieval from your own merged history is model-agnostic —
  the precedents *are* the company-specific knowledge, so the value holds
  precisely where general-purpose models degrade.

## Required Token Scopes

### GitHub Personal Access Token (GITHUB_TOKEN)

**Fine-grained PAT (preferred):**
- Repository permissions → Pull requests: Read-only
- Repository permissions → Issues: Read-only

**Classic PAT (public repos only):**
- `public_repo` (read-only access to public repository contents)

> Fine-grained PATs are preferred because they limit exposure to specific repositories and reduce the blast radius if a token is compromised.

### OpenAI API Key (OPENAI_API_KEY)

- Model access: `text-embedding-3-small` only
- No fine-tuning, no chat completions, no image generation required
- Consider using an API key restricted to the Embeddings endpoint if your OpenAI account supports key-level restrictions

## Security Notes

- **Never commit `.env`** — it is git-ignored. Use `.env.example` for placeholders.
- Secrets (`DATABASE_URL`, `GITHUB_TOKEN`, `OPENAI_API_KEY`) come **only from environment variables**.
- `senrah.yaml` holds non-secret tunables only. Any secret key in `senrah.yaml` will cause a startup error.

## Configuration

Non-secret tunables live in `senrah.yaml` (project root or any parent directory up to `.git`):

```yaml
project:
  name: my-project

repositories:
  - type: github
    name: owner/repo
    # Optional per-repo scope override:
    # scope: { mode: last_n, value: 200 }

ingest:
  # Default ingest scope for repos without a per-repo override.
  # Modes: all | last_n | since_date | period
  default_scope: { mode: last_n, value: 100 }
  # (The older `default_last_n: 100` key is still accepted for back-compat;
  #  `senrah init` migrates it to default_scope automatically.)

embed:
  model: text-embedding-3-small
  version: v1
  problem_limit_tokens: 1500
  diff_limit_tokens: 6000
  # Optional: any OpenAI-compatible endpoint (must return 1536-dim vectors)
  # base_url: https://openrouter.ai/api/v1

search:
  top_n: 5
  score_threshold: 0.40
  problem_weight: 0.6
  solution_weight: 0.4
  oversample_factor: 5
```

The CLI `--scope` flag on `senrah ingest` takes precedence over both the
per-repo and default config scopes. See `senrah.yaml.example` for the full set
of knobs (bot stop-lists, automation-title filters, giant-PR thresholds,
rate-limit floor, MCP host/port).

## Running Tests

```bash
# Unit tests only (no Docker required):
pytest tests/unit/ -x -q

# Full suite (requires Docker for pgvector container):
pytest tests/ -x -q
```

No test requires a real GitHub token or OpenAI key: integration tests run
against a real `pgvector/pgvector:pg17` testcontainer with a deterministic
fake embedder and mocked GitHub responses, including a true MCP-protocol E2E.

## Roadmap

Tracked openly rather than implied as done:

- **More connectors.** GitHub is the only connector today. The
  `ConnectorProtocol` seam is built (and import-boundary-tested) for GitLab,
  Bitbucket, and internal Git hosting, but no second connector ships yet.
- **Second-corpus validation.** Everything — retrieval quality, ingest
  behavior — is validated on `dotnet/efcore` only.
- **Forward-coverage number.** The leak-free temporal-holdout harness exists;
  reporting the full-power coverage metric is blocked on human labeling (the
  LLM judge failed its calibration gate — see [docs/EVAL.md](docs/EVAL.md)).
- **Distribution.** Releases currently publish to TestPyPI only (production
  PyPI is a deliberate manual gate); CI runs on Ubuntu + Python 3.12 only.
- **Serve-time config.** Relax startup validation so `senrah serve` no longer
  requires `GITHUB_TOKEN`, which the serve path never uses.

## License

MIT — see [LICENSE](LICENSE).
