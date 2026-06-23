# triage-v3.md -- Stage-2 Human Triage of v2 Misses (EVAL-03)

## Summary

19 original misses from results-v2-575-reindexed.json (misses_at_10).

Stage-1 mechanical reclassification (rule-driven, no LLM): 2 duplicates auto-identified.
  - These are missed-to-own-backport cases: top1 was a cluster member of the target
    that the v2 manifest did not list as relevant. Stage-1 caught them via the
    EVAL-01 cluster map.

Stage-2 human review of the remaining 17 residual misses: all 17 confirmed real-fail.
  - No label-error cases identified.
  - No further duplicates beyond the 2 Stage-1 reclassifications.
  - Conservative stance on 37194 (cluster [37194,37359]): frozen results store only
    top1, so no positive evidence 37359 ranked in top-10. Under D-11 (no silent number
    tuning), this is treated as real-fail.

Final tally: 2 duplicate, 17 real-fail, 0 label-error.

---

## One Row Per Miss

| target_pr | stage1_reclassified | final_tag | note |
|-----------|--------------------:|-----------|------|
| 36657 | false | real-fail | top1=37397 unrelated; genuine retrieval failure |
| 36708 | false | real-fail | top1=38322 unrelated; genuine retrieval failure |
| 36653 | false | real-fail | top1=37781 unrelated; genuine retrieval failure |
| 36723 | false | real-fail | top1=37262 unrelated; genuine retrieval failure |
| 36757 | false | real-fail | top1=37956 unrelated; genuine retrieval failure |
| 37197 | false | real-fail | relevant_prs=[37197,37198] already in v2 manifest; neither ranked top-10; top1=37690 unrelated |
| 37194 | false | real-fail | cluster=[37194,37359]; top1=37975 not in cluster; no evidence 37359 ranked (store only top1); conservative -> real-fail |
| 37207 | false | real-fail | top1=37949 unrelated; genuine retrieval failure |
| 37362 | false | real-fail | top1=37397 unrelated; genuine retrieval failure |
| 37350 | false | real-fail | top1=37975 unrelated; genuine retrieval failure |
| 37425 | false | real-fail | singleton cluster; top1=37284 unrelated; real-fail |
| 37390 | false | real-fail | top1=37257 unrelated; genuine retrieval failure |
| 37463 | false | real-fail | top1=37443 unrelated; genuine retrieval failure |
| 37474 | true  | duplicate | top1=37805 is cluster member of target; backport of its own target |
| 37392 | false | real-fail | top1=37788 unrelated; genuine retrieval failure |
| 37552 | false | real-fail | top1=37958 unrelated; genuine retrieval failure |
| 37762 | true  | duplicate | top1=37703 is cluster member of target; backport of its own target |
| 37783 | false | real-fail | top1=37690 unrelated; genuine retrieval failure |
| 37934 | false | real-fail | top1=37601 unrelated; genuine retrieval failure |
