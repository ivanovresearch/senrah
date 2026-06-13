# Senrah

Senrah indexes the merged-PR history of a codebase and serves it to AI coding agents over MCP. When an agent works on a task, it can retrieve real precedents — how similar problems were actually solved in *this* codebase — instead of guessing.

## Quick Start

### Prerequisites

- Python 3.12+
- Docker (for local Postgres+pgvector)

### Setup

1. **Start the database:**

   ```bash
   docker compose up -d
   ```

2. **Install senrah:**

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   ```

3. **Configure secrets** (copy `.env.example` → `.env`, fill in real values — never commit `.env`):

   ```bash
   cp .env.example .env
   # Edit .env with your real DATABASE_URL, GITHUB_TOKEN, OPENAI_API_KEY
   ```

4. **Configure your project** (copy `senrah.yaml.example` → `senrah.yaml`):

   ```bash
   cp senrah.yaml.example senrah.yaml
   # Edit senrah.yaml to point at your repos (no secrets here)
   ```

5. **Run migrations:**

   ```bash
   alembic upgrade head
   ```

6. **Ingest, index, and search:**

   ```bash
   senrah ingest
   senrah index
   senrah search "fix for cursor pagination in async resolver"
   ```

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
