# A/B Protocol — Harness uplift on real efcore tasks

**Frozen 2026-06-11, BEFORE any run.** Changes after the first run invalidate the result.

## Question

Does an agent solve a real task closer to the real merged fix WITH Harness
precedent retrieval than without? Weights are NOT touched (current config:
problem_weight 0.7 / solution_weight 0.3); this measures product uplift, not
retrieval tuning. The A/B result MUST NOT be used to pick weights.

## Held-out tasks (deterministic selection, frozen)

Rule, applied to the 306-PR corpus ordered by merged_at desc — first 12 with:
linked_issue present; title not matching
`source code updates|update dependencies|merging internal|\[automated\]` (i);
1–15 changed files; diff 100–6000 tokens (cl100k); ≥1 file `src/**/*.cs`.

Selected: 38367, 38271, 38251, 38208, 38226, 38140, 38344, 38252, 38321,
38297, 38286, 38260. All 12 linked issues verified accessible (closed, with
bodies). Task records: `eval/ab/tasks/task-<pr>.json`.

**Corpus hygiene — date cut, not list cut:** cutoff = merged_at(#38260) =
`2026-05-20 00:35:09+00`. ALL skills rows with merged_at >= cutoff are removed
from the searchable index (42 PRs: the 12 tasks + 30 newer neighbours,
including any backports of held-out fixes). Searchable corpus = 264 PRs.
Reversible: `harness index` re-embeds after the experiment.

## Workspace per task

efcore tree snapshot at the merge commit's parent (pre-fix state), **without
`.git`** — the merged fix exists in clone history and `git log` would leak it
to either arm. Both arms get the identical snapshot. Issue text query: all
`#NNNNN` tokens stripped (PR bodies embed `Fixes #N` — number-token leak).

## Arms

Same agent type, same prompt template, same budget; building/running efcore
is forbidden for BOTH arms (cost parity).

- **control:** issue title+body + snapshot. "Implement the fix, produce a patch."
- **treatment:** same + access to `harness search "<query>"` (Bash CLI);
  queries formulated by the agent itself.

Deviation #1 (recorded): spawned subagents cannot mount the MCP server;
treatment uses the `harness search` CLI. Output content mirrors search_prs_v1
(D-12). **Confidence signal verified present in CLI** (per-result
`score: 0.XXX`, `[BELOW THRESHOLD score=…]` + HINT on empty pass — D-11), so
treatment is NOT a weakened mode; only the debug-gated score components are
absent, as they are in default MCP output too.

## Outcome metrics — interpretation rules FROZEN BEFORE the run

1. **(a) file-recall** (share of real-fix files touched by the agent) and
   **(b) symbol-overlap** (changed methods/classes vs real diff) are THE
   primary numbers. Objective, automatic.
2. **(c) blind mechanism-similarity 0–3** (judge sees real diff + two
   anonymized solutions, order randomized, arm unknown) is SUPPORT, not
   primary. The judge is the same model family that generated the solutions —
   a confound. **If uplift appears only in (c) and not in (a)/(b), that is a
   red flag about the judge, not a win.** (c) is calibrated against a human
   read on 3–4 tasks before being cited.
3. **Categorical analysis is the compensation for outcome-metric blindness,
   not a supplement.** file-recall under-credits Harness where control
   guesses files anyway; the unique value (codebase conventions, category 2)
   shows in transcripts, not numbers. Small (a)/(b) delta + strong category-2
   evidence reads as "Harness helps in ways the metric can't see" — recorded
   as such, not as "no effect".

Rubric for (c): 0 = not a fix / wrong direction; 1 = plausible attempt,
different mechanism; 2 = same mechanism/place as the real fix; 3 = essentially
the real fix.

## Categories (per treatment run, from transcript + diffs) — all mandatory

1. precedent gave the solution shape (real uplift);
2. precedent gave a codebase convention the agent would not otherwise know;
3. precedent found but ignored/useless;
4. **precedent misled (negative uplift)** — REQUIRED category; an A/B that
   only counts help lies;
5. nothing useful found.

Report includes 2–3 concrete examples per occurring category (task, what
Harness returned, what each arm did).

## Headline

Per-task paired verdict on (a)+(b) (treatment closer / tie / control closer),
N=12 — catches the coarse signal (helps / no effect / harms) only. No weight
tuning before, during, or after based on this data.
