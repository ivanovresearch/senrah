# Changelog

All notable changes to senrah are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-06-13 — Release Readiness

Engineering hardening to make senrah ready for public use. No new retrieval
features.

### Added
- **Continuous Integration** (`.github/workflows/ci.yml`): unit, integration
  (testcontainers pgvector), and gitleaks secret-scan jobs run on every push and
  pull request.
- **Release pipeline** (`.github/workflows/release.yml`): pushing a `v*` tag
  builds an sdist + wheel and publishes to TestPyPI via OIDC Trusted Publishing
  (no stored token). See [RELEASING.md](RELEASING.md).
- **Single-source versioning**: the package version is read from installed
  metadata; added a `senrah --version` flag.
- **Documentation**: README clone→search quickstart, an MCP-client config
  example for connecting an agent to `senrah serve`, a `CONTRIBUTING.md`, and
  this changelog.

### Fixed
- **`senrah init`** now preserves standalone comments and block-list indentation
  in an existing `senrah.yaml` when upserting a repository entry (FIX-01).

## [1.0.0] — 2026-06-12 — GitHub-only MVP

First working version: index a codebase's merged-PR history and serve it to AI
coding agents over MCP.

### Added
- **Ingestion**: GitHub connector for merged PRs with resumable incremental
  traversal (durable cursor, rate-limit handling, bot/automation + giant-PR
  filtering).
- **Indexing**: OpenAI embeddings (`text-embedding-3-small`) into PostgreSQL +
  pgvector with HNSW (`vector_cosine_ops`) indexes on both problem and solution
  embeddings; token-aware truncation.
- **Retrieval**: `search_prs_v1` MCP tool (read-only, stateless) over stdio and
  streamable-HTTP; composite problem/solution similarity scoring with a
  configurable threshold and below-threshold hint.
- **CLI** (`senrah`): `init`, `ingest`, `index`, `search`, `serve`, `repos`,
  `status`.
- **Operations**: `index --reindex`, configurable tunables via `senrah.yaml`,
  status/observability surface, and a gitleaks pre-commit secret-scan hook.

[1.1.0]: https://github.com/ivanovresearch/senrah/releases/tag/v1.1.0
[1.0.0]: https://github.com/ivanovresearch/senrah/releases/tag/v1.0.0
