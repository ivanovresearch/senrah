"""
tests/integration/test_triage.py -- Integration tests for EVAL-03 Stage-1 triage.

Operates over the frozen v2 results (results-v2-575-reindexed.json) and the
frozen cluster map (eval/cluster/clusters.json).  No DB required; the triage
logic is pure Python over JSON files.

Asserts:
  1. triage-v3.json has exactly one row per original miss.
  2. The documented backport-miss class (top1 is a cluster member of target)
     is reclassified as stage1_reclassified=True.
  3. All rows have the required schema fields.
  4. final_tag is null for all rows (Stage 2 not yet complete).
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

EVAL_KNOWNITEM = pathlib.Path(__file__).parent.parent.parent / "eval" / "knownitem"
EVAL_CLUSTER = pathlib.Path(__file__).parent.parent.parent / "eval" / "cluster"
RESULTS_V2 = EVAL_KNOWNITEM / "results-v2-575-reindexed.json"
CLUSTERS_JSON = EVAL_CLUSTER / "clusters.json"


@pytest.fixture
def v2_misses():
    """Load the frozen v2 miss list."""
    data = json.loads(RESULTS_V2.read_text(encoding="utf-8"))
    return data["misses_at_10"]


@pytest.fixture
def triage_rows(tmp_path):
    """Run Stage-1 build_triage_v3 into a temp file and return rows."""
    from eval.knownitem.build_triage_v3 import build_triage_v3

    out = tmp_path / "triage-v3.json"
    rows = build_triage_v3(
        results_path=RESULTS_V2,
        clusters_path=CLUSTERS_JSON,
        out_path=out,
    )
    # Also verify the file was written and round-trips correctly
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert len(on_disk) == len(rows), "Written JSON should have same length as returned rows"
    return rows


class TestTriageRowCount:
    """One row per original miss -- no more, no fewer."""

    def test_row_count_matches_misses(self, v2_misses, triage_rows):
        assert len(triage_rows) == len(v2_misses), (
            f"Expected one row per miss ({len(v2_misses)}), "
            f"got {len(triage_rows)}"
        )

    def test_all_target_prs_present(self, v2_misses, triage_rows):
        triage_prs = {r["target_pr"] for r in triage_rows}
        assert triage_prs == set(v2_misses), (
            "Triage rows must cover exactly the original miss set"
        )


class TestTriageSchema:
    """Each row must carry the required fields."""

    REQUIRED_FIELDS = {
        "target_pr",
        "stage1_reclassified",
        "stage1_via_cluster",
        "top1_in_v2",
        "final_tag",
        "note",
    }

    def test_required_fields_present(self, triage_rows):
        for row in triage_rows:
            missing = self.REQUIRED_FIELDS - row.keys()
            assert not missing, (
                f"Row for PR {row.get('target_pr')} missing fields: {missing}"
            )

    def test_final_tag_is_null(self, triage_rows):
        """final_tag must be null at Stage-1 output (Stage-2 fills it)."""
        for row in triage_rows:
            assert row["final_tag"] is None, (
                f"PR {row['target_pr']}: final_tag should be null at Stage-1 "
                f"(got {row['final_tag']!r})"
            )

    def test_stage1_reclassified_is_bool(self, triage_rows):
        for row in triage_rows:
            assert isinstance(row["stage1_reclassified"], bool), (
                f"PR {row['target_pr']}: stage1_reclassified must be bool"
            )


class TestStage1Reclassification:
    """Stage-1 auto-reclassifies the backport-miss class correctly."""

    # These are the two confirmed backport-miss cases from the v2 results:
    #   37762: top1=37703, cluster [37703, 37762]
    #   37474: top1=37805, cluster [37474, 37805]
    # The v2 manifest only had the target as relevant; with the cluster map
    # the top1 IS a relevant member -- Stage-1 must reclassify these.
    EXPECTED_RECLASSIFIED = {37762, 37474}

    def test_documented_backport_cases_reclassified(self, triage_rows):
        """The confirmed backport-miss cases must be reclassified=True."""
        row_by_pr = {r["target_pr"]: r for r in triage_rows}
        for pr in self.EXPECTED_RECLASSIFIED:
            assert pr in row_by_pr, f"PR {pr} not found in triage rows"
            row = row_by_pr[pr]
            assert row["stage1_reclassified"] is True, (
                f"PR {pr} expected reclassified=True: "
                f"top1={row['top1_in_v2']} should be in cluster {row['stage1_via_cluster']}"
            )

    def test_reclassified_rows_have_cluster_info(self, triage_rows):
        """Reclassified rows must carry the cluster members for audit."""
        for row in triage_rows:
            if row["stage1_reclassified"]:
                assert row["stage1_via_cluster"] is not None, (
                    f"PR {row['target_pr']}: reclassified rows must record the cluster"
                )
                assert row["target_pr"] in row["stage1_via_cluster"], (
                    f"PR {row['target_pr']}: target must be in its own cluster"
                )

    def test_non_cluster_misses_not_reclassified(self, triage_rows):
        """Misses with top1 outside the target cluster remain stage1=False."""
        row_by_pr = {r["target_pr"]: r for r in triage_rows}
        # These misses have top1 that is NOT a cluster member of the target
        not_reclassified = [
            36657, 36708, 36653, 36723, 36757,
            37197, 37194, 37207, 37362, 37350,
            37425, 37390, 37463, 37392, 37552,
            37783, 37934,
        ]
        for pr in not_reclassified:
            assert pr in row_by_pr, f"PR {pr} not found in triage rows"
            row = row_by_pr[pr]
            assert row["stage1_reclassified"] is False, (
                f"PR {pr} expected reclassified=False but got True "
                f"(top1={row['top1_in_v2']}, cluster={row['stage1_via_cluster']})"
            )

    def test_reclassified_top1_is_cluster_member(self, triage_rows):
        """For reclassified rows, top1 must be in the same cluster as target."""
        from eval.cluster.grouping import cluster_of, load_cluster_map

        cluster_map = load_cluster_map(CLUSTERS_JSON)
        for row in triage_rows:
            if row["stage1_reclassified"]:
                target_cid = cluster_of(row["target_pr"], cluster_map)
                top1_cid = cluster_of(row["top1_in_v2"], cluster_map)
                assert target_cid == top1_cid, (
                    f"PR {row['target_pr']}: top1={row['top1_in_v2']} "
                    f"not in same cluster (target_cid={target_cid}, top1_cid={top1_cid})"
                )
