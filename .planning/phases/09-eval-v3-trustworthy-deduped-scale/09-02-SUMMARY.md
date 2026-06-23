---
phase: 09-eval-v3-trustworthy-deduped-scale
plan: "02"
subsystem: eval/cluster, eval/knownitem
tags: [cluster-grouping, per-cluster-dedup, triage, eval-02, eval-03]
dependency_graph:
  requires: [09-01]
  provides: [EVAL-02, EVAL-03]
  affects: [eval/cluster/grouping.py, eval/knownitem/triage-v3.json, eval/knownitem/triage-v3.md]
tech_stack:
  added: []
  patterns:
    - "Pure stdlib per-cluster collapse (cluster_of + collapse_per_cluster)"
    - "Stage-1 mechanical reclassification: missed-to-own-backport via cluster map (no LLM)"
    - "Stage-2 human triage: 17 residual misses -> real-fail; 2 Stage-1 duplicates confirmed"
    - "load_cluster_map thin I/O helper; counting functions accept parsed map (pure, no I/O)"
key_files:
  created:
    - eval/cluster/grouping.py
    - eval/knownitem/triage-v3.json
    - eval/knownitem/triage-v3.md
    - tests/unit/test_grouping.py
    - tests/integration/test_triage.py
  modified: []
decisions:
  - "Per-cluster deduplication: a hit on any cluster member = one cluster hit; distractors counted per-cluster (EVAL-02 / D-08)"
  - "Stage-1 auto-reclassification: 2 of 19 misses caught as missed-to-own-backport via cluster map (37762->37703, 37474->37805)"
  - "37194 conservative real-fail: cluster [37194,37359] known but frozen store has only top1; no positive evidence 37359 ranked (D-11 no silent number tuning)"
  - "37197 real-fail: v2 manifest already included relevant_prs=[37197,37198] (cluster was known at v2 time) but neither ranked in top-10"
  - "Final triage: 2 duplicate (Stage-1), 17 real-fail (Stage-2 human), 0 label-error"
metrics:
  duration: "~2 hours (includes human Stage-2 checkpoint)"
  completed: "2026-06-24"
  tasks: 3
  files: 5
---

# Phase 09 Plan 02: EVAL-02 + EVAL-03 Cluster Grouping and Miss Triage -- Summary

Pure per-cluster grouping module (EVAL-02) and two-stage triage of all 19 v2 misses (EVAL-03). Stage-1 mechanically reclassified 2 missed-to-own-backport cases via the EVAL-01 cluster map; Stage-2 human review confirmed 17 real retrieval failures. Every original miss now carries a recorded final_tag.

## What Was Built

### Per-cluster grouping module (`eval/cluster/grouping.py`)

Pure Python, no I/O in counting functions, no SQL. Provides:

- `cluster_of(pr_number, cluster_map) -> cluster_id`: returns the canonical cluster
  ID for a PR (the minimum member number for multi-member clusters; own number for
  singletons).
- `collapse_per_cluster(ranked_numbers, relevant_set, cluster_map) -> dict`: collapses
  a ranked result list so one cluster contributes at most one "relevant hit"; distractors
  are also deduplicated per-cluster. Returns `{"relevant": int, "distractor": int}`.
- `load_cluster_map(path) -> dict`: thin I/O helper that reads `clusters.json` and
  returns a `{pr_number: cluster_id}` dict. Counting functions accept the parsed map
  so they stay pure and importable by the Phase 10/11 temporal scorer.

Divergence fixture (`tests/unit/test_grouping.py`): a top-k containing two members of
one cluster -> per-PR count = 2, per-cluster count = 1. The test asserts the per-cluster
value equals 1. A stdlib-only import-constraint scan is included (modeled on
`test_scoring.py`).

### Stage-1 mechanical triage (`eval/knownitem/triage-v3.json` + `build_triage_v3.py`)

Reads `misses_at_10` from `results-v2-575-reindexed.json` (19 misses). For each miss,
re-scores via `grouping.cluster_of`: if top1 in v2 results is a cluster member of the
target, it is auto-reclassified as `stage1_reclassified=true` (the 37674->38066 class,
D-09). Two cases found:

- PR 37762: top1=37703, cluster [37703, 37762] -- v2 manifest lacked this edge.
- PR 37474: top1=37805, cluster [37474, 37805] -- v2 manifest lacked this edge.

All 19 rows written to `triage-v3.json` with `final_tag: null` at Stage-1 output.

Integration test (`tests/integration/test_triage.py`): asserts row count == miss count,
required schema fields present, both documented backport cases reclassified=true,
17 non-cluster misses remain reclassified=false.

### Stage-2 human triage (human checkpoint decisions applied)

Human reviewed all 17 residual misses (stage1_reclassified=false). Decisions:

- All 17 residual misses: `final_tag = "real-fail"`. Rationale:
  - 37425: singleton cluster, no other member possible.
  - 37197: both cluster members [37197,37198] were in v2 manifest but neither ranked
    in top-10 -- both are genuinely missed.
  - 37194: cluster [37194,37359] via linked-issue; frozen store has only top1 (not
    full top-10 membership); no positive evidence 37359 ranked. Conservative under
    D-11 (no silent number tuning) -> real-fail.
  - All other singletons: top1 is an unrelated PR -> genuine retrieval failures.
- No label-error cases identified.
- No further duplicates beyond the 2 Stage-1 reclassifications.

Stage-1 reclassified rows: `final_tag = "duplicate"` (backport of own target).

Final tally: 2 duplicate, 17 real-fail, 0 label-error.

Human-readable summary written to `eval/knownitem/triage-v3.md` (one line per miss,
counts in header).

## Verification

```
pytest tests/unit/test_grouping.py tests/integration/test_triage.py -q
# 25 passed
```

- `TestGroupingModule` (test_grouping.py): cluster_of singletons, cluster members,
  collapse_per_cluster basic, divergence fixture (per-PR=2 vs per-cluster=1 asserted),
  stdlib-only import scan.
- `TestTriageRowCount` (test_triage.py): row count == miss count, all target PRs present.
- `TestTriageSchema`: required fields, final_tag null, stage1_reclassified bool.
- `TestStage1Reclassification`: documented backport cases reclassified=true, cluster info
  present, non-cluster misses reclassified=false, top1 in same cluster as target.

## Deviations from Plan

None -- plan executed exactly as written. Stage-2 decisions delivered via human
checkpoint as designed. Conservative stance on 37194 applied per D-11.

## Known Stubs

None -- all 19 rows carry a non-null final_tag. triage-v3.json is complete.

## Threat Flags

None -- triage artifacts contain PR numbers and tags only; no secrets.

## Self-Check: PASSED

Files verified:
- FOUND: eval/cluster/grouping.py
- FOUND: eval/knownitem/triage-v3.json
- FOUND: eval/knownitem/triage-v3.md
- FOUND: tests/unit/test_grouping.py
- FOUND: tests/integration/test_triage.py

Commits verified:
- FOUND: 6eb5b02 (EVAL-02 grouping module)
- FOUND: 5479f26 (Stage-1 triage, triage-v3.json)
- FOUND: 257970c (Stage-2 human decisions, triage-v3.md)

All 19 rows in triage-v3.json carry non-null final_tag (2 duplicate, 17 real-fail).
25 tests pass.
