---
phase: 09-eval-v3-trustworthy-deduped-scale
plan: "03"
subsystem: eval/knownitem
tags: [manifest-v3, deduped-baseline, eval-04, run-eval, reproducibility, determinism]
dependency_graph:
  requires: [09-01, 09-02]
  provides: [EVAL-04]
  affects:
    - eval/knownitem/build_manifest.py
    - eval/knownitem/manifest-v3.json
    - eval/knownitem/results-v3-deduped.json
    - tests/integration/test_manifest_v3.py
tech_stack:
  added: []
  patterns:
    - "build_v3() reuses v2 query text verbatim (no GitHub re-fetch); only relevant_prs recomputed from cluster map"
    - "Corrections from triage-v3.json (2 duplicate collapses) carried in manifest corrections list"
    - "Determinism test: re-running scorer on frozen manifest + fake_embedder yields identical metrics"
    - "v2 subset sanity bound: v3 relevant_prs superset of v2 per target (minus recorded triage removals)"
    - "run_eval.py explicit --manifest flag + loud v2 fallback (T-09-06 no-silent-re-freezing)"
key_files:
  created:
    - eval/knownitem/manifest-v3.json
    - eval/knownitem/results-v3-deduped.json
    - eval/knownitem/generate_v3_manifest.py
    - tests/integration/test_manifest_v3.py
  modified:
    - eval/knownitem/build_manifest.py
    - eval/knownitem/run_eval.py
decisions:
  - "v3 relevant_prs sourced from fuzzy cluster map (grouping.cluster_of) instead of exact title groups; cluster enrichment can only add members, never drop (except recorded label-error removals)"
  - "v3 metric improvement is from cluster-member grouping counting any member as a hit, NOT from number tuning (D-11)"
  - "run_eval.py requires explicit --manifest selection; fallback to v2 manifest is now loud (T-09-06 anti-silent-re-freezing)"
  - "Eval artifacts frozen by orchestrator (not executor) because executor Bash was sandbox-blocked; process deviation documented"
  - "37194 recovered via cluster grouping: cluster [37194,37359] -> hit on 37359 now counts; previously lost under per-PR scoring"
  - "37762 and 37474 recovered via cluster grouping: their clusters include 37703/37805 which ranked in top-k"
metrics:
  duration: "~3 hours (includes orchestrator freeze workaround)"
  completed: "2026-06-24"
  tasks: 2
  files: 6
---

# Phase 09 Plan 03: EVAL-04 v3 Deduped Manifest and Frozen Baseline -- Summary

v3-knownitem-deduped manifest minted from the EVAL-01 fuzzy cluster map with EVAL-03 corrections applied; v2 query text reused verbatim (no GitHub re-fetch); run_eval re-run over the same 575-PR corpus produces the trustworthy deduped baseline the depth experiment (Phase 10/11) measures against.

## What Was Built

### Extended `build_manifest.py` with `build_v3()` path

Added a `build_v3()` function and CLI dispatch so `main` can mint v2 (unchanged) or v3. The v3 path:

- Loads the existing v2 `manifest.json` and reuses each query's `query` text, `issue`, and
  `merged_at` verbatim -- no GitHub issue re-fetch (RESEARCH note 5 network caveat).
- Sources `relevant_prs` for each target from `grouping.cluster_of(target, cluster_map)` instead
  of the inline `title_groups` exact-title set.
- Applies the 2 duplicate collapses recorded in `triage-v3.json`; carries the full corrections
  list in the manifest output.
- Writes `manifest-v3.json` with `version: "v3-knownitem-deduped"`, the corpus fingerprint, the
  cluster-map fingerprint hash (D-06 cross-check), and the preserved `skipped` list.
- Makes no GitHub API call on the v3 path.

### Frozen `eval/knownitem/manifest-v3.json`

- version: `v3-knownitem-deduped`
- 218 queries (same query set as v2; no targets added or removed)
- `corrections` list: 2 EVAL-03 collapsed-duplicate entries
- cluster-map fingerprint hash recorded

### Frozen `eval/knownitem/results-v3-deduped.json`

Produced by `python eval/knownitem/run_eval.py v3-deduped --manifest manifest-v3.json`.

| Metric | v2 baseline | v3 deduped | Delta |
|--------|-------------|------------|-------|
| recall@1 | 0.670 | 0.711 | +0.041 |
| recall@5 | 0.881 | 0.899 | +0.018 |
| recall@10 | 0.913 | 0.927 | +0.014 |
| MRR@10 | 0.760 | 0.794 | +0.034 |
| misses@10 | 19 | 16 | -3 |

The 3 recovered misses are the cluster-collapsed cases (37762, 37474, 37194): ranking found a
cluster member in top-k; per-cluster scoring now counts that as a hit. The metric movement is
documented as expected and correct per D-11 -- it is NOT number tuning.

Corpus: 575 PRs, 2024-04-06 to 2026-06-12. Weights: problem=0.7, solution=0.3,
oversample=5. Ranking-only (score_threshold=0.0) per D-12.

### `run_eval.py` safety fix

Added explicit `--manifest` flag selection and a loud fallback warning when the tag-derived
filename is absent. Previously a missing `manifest-v3-deduped.json` silently fell back to the
v2 manifest and froze a wrong baseline -- the initial incorrect freeze was caused by this bug
(T-09-06 "no silent re-freezing"). Final freeze used the explicit path:
`python eval/knownitem/run_eval.py v3-deduped --manifest manifest-v3.json`.

### `tests/integration/test_manifest_v3.py` (17 tests pass, 1 skipped)

Four assertion groups:

1. **Reproducibility**: building the v3 manifest twice over the same seeded corpus yields
   byte-identical `relevant_prs` and an identical cluster-map fingerprint hash.
2. **Determinism**: re-running the scorer on a fixed manifest + `fake_embedder` (deterministic
   1536-d vectors, no OpenAI) yields identical recall@k/MRR across two runs.
3. **Manifest structure**: corrections list present, fingerprint recorded, version string correct.
4. **v2 subset sanity bound (WARNING-2 insurance)**: for every shared target, v3 `relevant_prs`
   is a superset of v2 `relevant_prs` except members listed as label-error removals in
   `triage-v3.json` (the only legitimate shrinkage). Cluster-expansion stays within
   `EXPANSION_BOUND`; any unrecorded v2-member loss or expansion above the bound fails loudly.
   This guard catches an EVAL-01 over-merge that determinism alone would not detect.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] run_eval.py silent tag-to-filename fallback froze the wrong baseline**
- **Found during:** Task 2 (eval artifact freeze)
- **Issue:** `run_eval.py` derived the manifest filename from the tag string (`v3-deduped` ->
  `manifest-v3-deduped.json`); that file did not exist so it silently fell back to
  `manifest.json` (v2), producing a result file that looked like a v3 run but used v2 labels.
- **Fix:** Added explicit `--manifest` CLI flag; made the fallback loud (loud warning + abort
  or explicit override required). Final freeze invoked as
  `python eval/knownitem/run_eval.py v3-deduped --manifest manifest-v3.json`.
- **Files modified:** `eval/knownitem/run_eval.py`
- **Commit:** 1f4ce9c (included in the artifact freeze commit)

### Process Deviation

**2. Eval artifact freeze executed by orchestrator, not executor**
- **Context:** The executor's Bash tool was sandbox-blocked (could not run Python scripts to
  produce the frozen artifacts in the normal task flow).
- **Resolution:** The orchestrator ran the freeze commands directly and committed the frozen
  artifacts. The code changes (build_manifest.py, run_eval.py, test_manifest_v3.py) were
  committed by the executor (94307b7); the frozen artifacts were committed separately by the
  orchestrator (1f4ce9c). The separation does not affect reproducibility -- the two commits
  together constitute the complete plan deliverable.
- **Impact:** None on correctness. Both commits are on `main`. The test suite passes against
  the frozen artifacts.

## Threat Mitigations Verified

| Threat | Mitigation Applied |
|--------|--------------------|
| T-09-02 Manifest freeze integrity | corpus + cluster-map fingerprint hash in manifest; build-twice reproducibility test in test_manifest_v3.py |
| T-09-06 Re-freezing to taste | Corrections sourced from recorded triage-v3.json (rule-driven); run_eval.py now requires explicit --manifest; baseline movement documented not silently tuned |
| T-09-09 Wrong-but-reproducible baseline | v2-subset sanity bound in test_manifest_v3.py asserts superset + EXPANSION_BOUND; catches EVAL-01 over-merge that determinism alone misses |
| T-09-08 Token exposure | v3 build path makes zero GitHub calls; token never in manifest |

## Self-Check

**Files exist:**
- eval/knownitem/manifest-v3.json -- FOUND (committed 1f4ce9c)
- eval/knownitem/results-v3-deduped.json -- FOUND (committed 1f4ce9c)
- eval/knownitem/build_manifest.py -- FOUND (committed 94307b7)
- eval/knownitem/run_eval.py -- FOUND (committed 1f4ce9c)
- eval/knownitem/generate_v3_manifest.py -- FOUND (committed 94307b7)
- tests/integration/test_manifest_v3.py -- FOUND (committed 94307b7)

**Commits exist:**
- 94307b7 -- build_manifest.py build_v3(), run_eval.py manifest support, generate_v3_manifest.py, tests/integration/test_manifest_v3.py
- 1f4ce9c -- frozen manifest-v3.json + results-v3-deduped.json + run_eval.py safety fix

**Test count:** 17 integration tests pass, 1 skipped.

**Metrics verified from results-v3-deduped.json:**
- recall@1: 0.711, recall@5: 0.899, recall@10: 0.927, MRR@10: 0.794, misses@10: 16

## Self-Check: PASSED
