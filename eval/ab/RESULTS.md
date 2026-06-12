# A/B Results — Harness uplift on 12 real efcore tasks

Run 2026-06-11..12 per the frozen `PROTOCOL.md`. All 24 runs completed; every
treatment run verifiably queried the search tool (query log + self-report).
Weights untouched (0.7/0.3, threshold 0.45). Worker model: sonnet, both arms.

## Paired verdicts — primary metrics (a) file_recall / (b) symbol_recall

Verdict rule (frozen before the last two pairs landed): file_recall decides;
if equal, symbol_recall decides; |Δ| < 0.05 = tie.

| Task | control (file/sym) | treatment (file/sym) | Verdict | Precedent category |
|---|---|---|---|---|
| 38251 TVP ToQueryString | 0.50 / 0.125 | 0.50 / 0.083 | tie | 3 |
| 38260 SIGN cast | 1.00 / 0.714 | 0.60 / 0.619 | control | 3 |
| 38208 EF1003 analyzer | 1.00 / 0.286 | 1.00 / 0.381 | treatment | 3 |
| 38367 complex-type CreateEntry | 0.18 / 0.240 | 0.18 / 0.080 | control | 3 |
| 38271 DROP_EXISTING index | 0.33 / 0.091 | 1.00 / 0.424 | treatment | **2** (#37652 exclusion pattern) |
| 38226 List.Exists→Any | 0.75 / 0.565 | 0.25 / 0.391 | control | 3 |
| 38140 GroupBy EmptyProjectionMember | 0.25 / 0.296 | 0.50 / 0.333 | treatment | 3 (weak 2: "confirmed area") |
| 38252 computed-column ALTER | 0.00 / 0.190 | 0.00 / 0.190 | tie | 3 (weak 2: level choice via #38019) |
| 38344 SQLite aggregate ORDER BY | 0.83 / 0.689 | 0.83 / 0.622 | control | 3 |
| 38321 JSON column on view | 0.50 / 0.167 | 0.50 / 0.375 | treatment | 3 |
| 38286 ValuesExpression null mapping | 0.25 / 0.273 | 0.13 / 0.545 | control | 3 |
| 38297 Cosmos top-level Any | 1.00 / 0.861 | 1.00 / 0.778 | control | **2** (#37603 WithIn root-query pattern) |

**Tally: control 6 · treatment 4 · tie 2.**
Means: file_recall 0.55 (control) vs 0.54 (treatment); symbol_recall 0.37 vs 0.40.
A 6:4:2 split at N=12 is statistically a dead heat (two-sided sign test on 10
non-ties: p ≈ 0.75).

## Category distribution (12 valid treatment runs)

1. **Exact solution shape: 0.**
2. **Codebase convention: 2 clear + 2 weak.** 38297 (reused #37603's
   `Sources is [{ WithIn: false }, ..]` root-query detection), 38271 (adopted
   #37652's pattern of excluding full-text/vector/JSON indexes from a DDL
   transform). Weak: 38140, 38252 ("confirmed the area / the level").
3. **Found but useless: 8.** Scores clustered 0.31–0.49, mostly below the
   0.45 threshold; agents read the [BELOW THRESHOLD] flag and ignored.
4. **Misled (negative uplift): 0.** No treatment run was pulled off-course by
   a precedent. The confidence flag did its protective job.
5. (Nothing returned at all: never — folded into 3.)

## Judge metric (c)

NOT cited per protocol: requires human calibration on 3–4 tasks first. Note
that in 9/12 pairs both arms converged on essentially the same fix, so (c)
would add little discrimination here anyway.

## Reading (per the three frozen interpretation rules)

1. **(a)/(b) primary:** no uplift and no harm on outcome metrics. The product
   neither helped nor hurt on this task profile.
2. **(c) support:** unused (no calibration) — and unnecessary; (a)/(b) agree.
3. **Categories as compensation:** the metric-invisible value did appear —
   twice the precedent carried a real codebase convention into the fix, and
   both times that pair ALSO scored well (38271 treatment 1.0 file_recall;
   38297 1.0/0.778). The dominant failure is not ranking but **content**:
   a 264-PR / 3.5-month corpus rarely contains a genuine precedent for a novel
   bug, so the correct answer to most queries is "nothing relevant" — which the
   system honestly reports and agents honestly respect.

## Why no uplift — and what it implies (NOT weights)

- **Corpus depth.** 264 PRs ≈ 3.5 months of history. Both category-2 hits came
  from the only deep-history precedents that existed. The single highest-score
  result that got USED (0.51) was also the only above-threshold hit that was
  topically right. Implication: ingest years, not months (`--scope all` is now
  safe post-gate-#1).
- **Task profile.** The 12 issues are well-specified bug reports that already
  pin the fix location — exactly where a strong agent needs no precedent.
  Vaguer feature-shaped tasks (where conventions matter most) are
  underrepresented by the deterministic filter.
- **Not weights.** Zero category-4 and a dead-heat (a)/(b) mean re-ranking the
  same shallow corpus cannot create uplift. Phase 4 weight tuning is
  second-order until corpus depth changes what there is to rank.

## Costs / integrity notes

- Session limits killed and forced re-runs of 13 agent runs (no data
  contamination: every killed run's directory was /MIR-reset before retry).
- One treatment run (38271, attempt 2) completed without tool access
  (sandbox denial) and was discarded + re-run; its discarded scores are not in
  the table.
- Both arms same model, same prompt template, same budget instruction; diffs
  scored by the same extractor (`score.py`); raw patches and per-run scores in
  `eval/ab/runs/`.
