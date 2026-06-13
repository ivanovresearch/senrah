# Contributing to senrah

Thanks for your interest in improving senrah. This guide covers local setup,
running the tests, and the secret-scan hook.

## Dev setup

```bash
git clone https://github.com/Vladimir1Ivanov/senrah.git
cd senrah
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Copy the example config files and fill in your own values (never commit real
secrets — see the [README](README.md#security-notes)):

```bash
cp .env.example .env
cp senrah.yaml.example senrah.yaml
```

## Running the tests

**Unit tests** — fast, no Docker required:

```bash
pytest tests/unit -q
```

**Full suite (unit + integration)** — requires Docker. The integration tests
self-provision a `pgvector/pgvector:pg17` database via
[testcontainers](https://testcontainers.com/); no manual DB setup is needed.

```bash
pytest tests -q
```

On **Windows**, the testcontainers Ryuk reaper can fail to publish its port
mapping. Set this before running the full suite:

```bash
# PowerShell
$env:TESTCONTAINERS_RYUK_DISABLED = "true"
# bash
export TESTCONTAINERS_RYUK_DISABLED=true
```

The integration/E2E tests use a fake embedder and dummy credentials — no real
`OPENAI_API_KEY` or `GITHUB_TOKEN` is required to run them.

## Secret-scan hook (gitleaks)

A pre-commit hook scans staged changes with [gitleaks](https://github.com/gitleaks/gitleaks)
and blocks any commit containing a token-shaped string. It is **not** enabled
automatically on clone — turn it on once per clone:

```bash
git config core.hooksPath .githooks
```

Install the `gitleaks` binary so it resolves on your `PATH` (or drop a release
binary into the gitignored `tools/gitleaks/` directory). The hook **fails
closed** — if the scanner is missing, the commit is blocked rather than waved
through. This mirrors the server-side `gitleaks` job in CI.

## Continuous Integration

`.github/workflows/ci.yml` runs on every push and pull request:

- **unit** — `pytest tests/unit`
- **integration** — `pytest tests` (testcontainers pgvector)
- **gitleaks** — server-side secret scan

All three must be green to merge. CI runs on Ubuntu + Python 3.12 (no OS/version
matrix in v1.1).

## Releases

Releases are cut from version tags and published to TestPyPI. See
[RELEASING.md](RELEASING.md) for the version-bump and tagging process.

## Known future hardening

GitHub Actions are currently pinned to major-version tags (`@v4`, `@v5`,
`@release/v1`). Pinning to full commit SHAs is a planned supply-chain hardening
step.
