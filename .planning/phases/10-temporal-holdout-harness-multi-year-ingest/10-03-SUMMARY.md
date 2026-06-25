# 10-03 SUMMARY — DEPTH-01 deep ingest + clusters-deep + define_split.py

**Plan:** 10-03 | **Status:** COMPLETE (all 3 tasks) | **Date:** 2026-06-25

## What was built

| Task | Result |
|------|--------|
| 1. Multi-year `--scope all` ingest + index (human-action) | efcore **487 → 8449** PR / **8449** skills (exact match, no data loss). 1 PR (#31923) skipped on a NUL-byte (0x00) error — non-fatal. `senrah.yaml` also has `encode/httpx` → it ingested too (1145 rows); table total 9594. efcore `merged_at` span **2014-02-05 → 2026-06-24** (12+ yr). |
| 2. `build_clusters.py --out` flag + `clusters-deep.json` | flag added (commit `2a54ce9`). `clusters-deep.json` built over the full 9594-row table: **9035 clusters, 397 multi-member**, hash `5bc78aab142fa1d8`, 353 KB. `clusters.json` UNCHANGED (hash `e5ed8bdb…` before+after — Phase 9 artifact intact). Consistent with Phase 9 (its `clusters.json` already spanned both repos at 575 rows). |
| 3. `eval/temporal/__init__.py` + `define_split.py` | committed `455d03a`; imports clean. |

## Row counts (recorded per plan)
- before: pull_requests(efcore)=487, skills(efcore)=487
- after:  pull_requests(efcore)=8449, skills(efcore)=8449 ; table total 9594/9594
- clusters-deep.json: prs=9594, clusters=9035, multi-member=397

## Verification
- DB authoritative counts confirmed (efcore 8449/8449; table 9594/9594).
- `clusters.json` byte-identical before/after (hash pinned).
- `clusters-deep.json` is the frozen deep cluster map; **committed (tracked like `clusters.json`)** per the 10-03 plan fix — it is a hash-pinned artifact `manifest-temporal.json` will reference.
- 335 unit tests pass (no regressions).

## ⚠ Carry-forward finding (affects 10-04/10-05, NOT this plan's artifacts)
Running the metadata-only `answerable` label from `define_split.py` over the deep corpus gives
**n_answerable = 5 / 278** at T=365 (4 at 455/545) — far below the 80 floor. The A-vs-B precedent
probe (30-sample) found a genuine pre-T precedent for ≥14/30 (strict)…~29/30 (lenient): the metadata
label (linked-issue OR backport-cluster) structurally misses **unlinked convention-transfer
precedents**. Verdict A: label too narrow, temporal-holdout rescuable. Resolution: a **leak-aware
TREC-pooled, judge-labeled relevance protocol** (see `10-TEMPORAL-RELEVANCE-PROTOCOL.md`), which
re-scopes 10-04 (relevance definition) and pulls the calibrated judge into Phase 10. The metadata
path (n=5) is retained as a recorded baseline, not the gate.

## Commits
- `2a54ce9` build_clusters --out flag
- `455d03a` define_split.py + package marker
- (this) clusters-deep.json + SUMMARY
