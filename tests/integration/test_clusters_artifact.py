"""
tests/integration/test_clusters_artifact.py — Integration tests for clusters.json builder.

Uses pg_dsn_migrated + seeded pull_requests rows to assert:
  1. A known backport pair lands in the same cluster.
  2. A distinct PR is in its own cluster.
  3. The artifact carries a non-empty corpus_fingerprint.hash.
  4. Rebuilding over the same seeded corpus yields an identical hash (determinism).
  5. The artifact schema has version, corpus_fingerprint.hash, params.sim_threshold,
     clusters, and edges keys.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from datetime import datetime, timezone

import psycopg
import pytest


# ── Helpers ─────────────────────────────────────────────────────────────────

def _seed_pull_requests(dsn: str, rows: list[dict]) -> None:
    """Insert minimal pull_requests rows for tests.

    Follows the schema: projects → repositories → pull_requests (FK chain).
    Column name is 'repository_id' in pull_requests (not 'repo_id').
    """
    with psycopg.connect(dsn) as conn:
        # Ensure project exists.
        project_row = conn.execute(
            "INSERT INTO projects (name) VALUES ('testproject') "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
        ).fetchone()
        project_id = project_row[0]

        # Ensure repository exists.
        repo_row = conn.execute(
            "INSERT INTO repositories (project_id, type, name) VALUES (%s, 'github', 'testrepo') "
            "ON CONFLICT (project_id, name) DO UPDATE SET type = EXCLUDED.type RETURNING id",
            (project_id,),
        ).fetchone()
        repository_id = repo_row[0]

        for r in rows:
            conn.execute(
                """
                INSERT INTO pull_requests
                  (repository_id, number, title, body, diff, author, merged_at, linked_issue, files_changed, content_hash)
                VALUES
                  (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (repository_id, number) DO NOTHING
                """,
                (
                    repository_id,
                    r["number"],
                    r.get("title", ""),
                    r.get("body", ""),
                    r.get("diff", ""),
                    r.get("author", "contributor"),
                    r.get("merged_at", datetime(2024, 1, 1, tzinfo=timezone.utc)),
                    r.get("linked_issue", None),
                    json.dumps(r.get("files_changed", [])),
                    r.get("content_hash", f"hash{r['number']}"),
                ),
            )


# ── Test fixtures ─────────────────────────────────────────────────────────────

# A known backport pair: same normalized title (branch prefix stripped).
BACKPORT_PAIR = [
    {
        "number": 37674,
        "title": "Fix query plan regression in Foo",
        "body": "Fixes a performance regression in query plan generation.",
        "diff": "+    var optimized = GetOptimizedPlan();\n-    var optimized = GetSlowPlan();",
        "author": "dev",
        "merged_at": datetime(2024, 1, 10, tzinfo=timezone.utc),
        "linked_issue": None,
        "files_changed": ["src/EFCore/Query/QueryPlanCache.cs"],
    },
    {
        "number": 38066,
        "title": "[release/8.0] Fix query plan regression in Foo",
        "body": "Backport of the query plan fix to 8.0.",
        "diff": "+    var optimized = GetOptimizedPlan();\n-    var optimized = GetSlowPlan();",
        "author": "dev",
        "merged_at": datetime(2024, 1, 12, tzinfo=timezone.utc),
        "linked_issue": None,
        "files_changed": ["src/EFCore/Query/QueryPlanCache.cs"],
    },
]

# A distinct PR: different title, no relation to the backport pair.
DISTINCT_PR = {
    "number": 99001,
    "title": "Add async support for BlobStorageConnector",
    "body": "Implements async blob storage operations.",
    "diff": "+    await blobClient.UploadAsync(stream);",
    "author": "other-dev",
    "merged_at": datetime(2024, 2, 1, tzinfo=timezone.utc),
    "linked_issue": None,
    "files_changed": ["src/EFCore.Storage/Blob/BlobStorageConnector.cs"],
}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestClusterArtifact:
    """Integration tests for build_cluster_artifact."""

    def test_known_backport_pair_shares_cluster(self, pg_dsn_migrated: str):
        """The known backport pair (37674/38066) must land in one cluster."""
        from eval.cluster.build_clusters import build_cluster_artifact

        _seed_pull_requests(pg_dsn_migrated, BACKPORT_PAIR + [DISTINCT_PR])

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = pathlib.Path(f.name)

        artifact = build_cluster_artifact(pg_dsn_migrated, dry_run=True, use_refetch=False, out=out)

        # Find the cluster containing 37674.
        cluster_37674 = next(
            (c for c in artifact["clusters"] if 37674 in c), None
        )
        assert cluster_37674 is not None, "PR 37674 must be in some cluster"
        assert 38066 in cluster_37674, (
            f"38066 must be in the same cluster as 37674, got cluster: {cluster_37674}"
        )

    def test_distinct_pr_in_own_cluster(self, pg_dsn_migrated: str):
        """The distinct PR (99001) must be in its own cluster."""
        from eval.cluster.build_clusters import build_cluster_artifact

        _seed_pull_requests(pg_dsn_migrated, BACKPORT_PAIR + [DISTINCT_PR])

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = pathlib.Path(f.name)

        artifact = build_cluster_artifact(pg_dsn_migrated, dry_run=True, use_refetch=False, out=out)

        cluster_99001 = next(
            (c for c in artifact["clusters"] if 99001 in c), None
        )
        assert cluster_99001 is not None, "PR 99001 must be in some cluster"
        assert cluster_99001 == [99001], (
            f"Distinct PR 99001 must be in its own singleton cluster, got: {cluster_99001}"
        )

    def test_artifact_has_non_empty_hash(self, pg_dsn_migrated: str):
        """The artifact must carry a non-empty corpus_fingerprint.hash."""
        from eval.cluster.build_clusters import build_cluster_artifact

        _seed_pull_requests(pg_dsn_migrated, BACKPORT_PAIR + [DISTINCT_PR])

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = pathlib.Path(f.name)

        artifact = build_cluster_artifact(pg_dsn_migrated, dry_run=True, use_refetch=False, out=out)

        assert artifact["corpus_fingerprint"]["hash"], "corpus_fingerprint.hash must be non-empty"
        # SHA-256 hex digest is 64 characters.
        assert len(artifact["corpus_fingerprint"]["hash"]) == 64

    def test_rebuild_determinism(self, pg_dsn_migrated: str):
        """Rebuilding over the same seeded corpus yields an identical corpus_fingerprint.hash."""
        from eval.cluster.build_clusters import build_cluster_artifact

        _seed_pull_requests(pg_dsn_migrated, BACKPORT_PAIR + [DISTINCT_PR])

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out1 = pathlib.Path(f.name)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out2 = pathlib.Path(f.name)

        artifact1 = build_cluster_artifact(pg_dsn_migrated, dry_run=True, use_refetch=False, out=out1)
        artifact2 = build_cluster_artifact(pg_dsn_migrated, dry_run=True, use_refetch=False, out=out2)

        assert artifact1["corpus_fingerprint"]["hash"] == artifact2["corpus_fingerprint"]["hash"], (
            "Rebuilding over the same corpus must yield an identical hash (D-06 determinism)"
        )

    def test_artifact_schema_keys(self, pg_dsn_migrated: str):
        """Artifact must have version, corpus_fingerprint.hash, params.sim_threshold, clusters, edges."""
        from eval.cluster.build_clusters import build_cluster_artifact

        _seed_pull_requests(pg_dsn_migrated, BACKPORT_PAIR + [DISTINCT_PR])

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = pathlib.Path(f.name)

        artifact = build_cluster_artifact(pg_dsn_migrated, dry_run=True, use_refetch=False, out=out)

        assert artifact.get("version") == "v3", "version must be 'v3'"
        assert "corpus_fingerprint" in artifact
        assert "hash" in artifact["corpus_fingerprint"]
        assert "params" in artifact
        assert "sim_threshold" in artifact["params"]
        assert "clusters" in artifact
        assert "edges" in artifact

    def test_artifact_written_to_disk(self, pg_dsn_migrated: str):
        """The artifact must be written to the output path as valid JSON."""
        from eval.cluster.build_clusters import build_cluster_artifact

        _seed_pull_requests(pg_dsn_migrated, BACKPORT_PAIR + [DISTINCT_PR])

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = pathlib.Path(f.name)

        build_cluster_artifact(pg_dsn_migrated, dry_run=True, use_refetch=False, out=out)

        assert out.exists(), "clusters.json must be written to disk"
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["version"] == "v3"
        assert loaded["corpus_fingerprint"]["hash"]
