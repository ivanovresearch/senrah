# Senrah

[![CI](https://github.com/Vladimir1Ivanov/senrah/actions/workflows/ci.yml/badge.svg)](https://github.com/Vladimir1Ivanov/senrah/actions/workflows/ci.yml)

Senrah indexes the merged-PR history of a codebase and serves it to AI coding agents over MCP. When an agent works on a task, it can retrieve real precedents — how similar problems were actually solved in *this* codebase — instead of guessing.

## Quick Start

### Prerequisites

- Python 3.12+
- Docker (for local Postgres+pgvector)

### Setup

1. **Clone the repository:**

   ```bash
   git clone https://github.com/Vladimir1Ivanov/senrah.git
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

6. **Run migrations:**

   ```bash
   alembic upgrade head
   ```

7. **Ingest, index, and search:**

   ```bash
   senrah ingest
   senrah index
   senrah search "fix for cursor pagination in async resolver"
   ```

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
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

`OPENAI_API_KEY` is required because the server embeds the incoming query;
`GITHUB_TOKEN` is **not** needed at serve time (the server is read-only over the
database). Point `DATABASE_URL` at the same database you ingested and indexed
into.

For a remote setup, run `senrah serve --transport network` instead — a
streamable-HTTP server that binds `127.0.0.1` by default (use `--host 0.0.0.0`
only when you intentionally expose it to a shared network).

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

ingest:
  default_last_n: 100    # PRs to fetch per ingest run

embed:
  model: text-embedding-3-small
  version: v1
  problem_limit_tokens: 1500
  diff_limit_tokens: 6000

search:
  top_n: 5
  score_threshold: 0.40
  problem_weight: 0.6
  solution_weight: 0.4
  oversample_factor: 5
```

## Running Tests

```bash
# Unit tests only (no Docker required):
pytest tests/unit/ -x -q

# Full suite (requires Docker for pgvector container):
pytest tests/ -x -q
```

## License

MIT — see [LICENSE](LICENSE).
