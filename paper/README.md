# Paper: Ranking Is Not the Bottleneck (Precedent Retrieval, Leak-Free Eval)

Working draft of the methodology paper based on [docs/EVAL.md](../docs/EVAL.md).

**Target venues** (in order of preference):

1. **arXiv preprint** (cs.SE, cross-list cs.IR) — publish as soon as the draft
   is complete; first-time submitters in cs.SE need an endorsement
   (https://arxiv.org/help/endorsement).
2. **MSR** (Mining Software Repositories, msrconf.org) — technical track or
   Data & Tool Showcase; deadlines are typically December–February for the
   May conference. Check the MSR 2027 CFP.
3. **EMSE journal** (Empirical Software Engineering) — rolling submission,
   no deadline pressure.

**Build**: `latexmk -pdf main.tex`, or upload this directory to Overleaf
(the `acmart` class is preinstalled there).

**Status**: v0.3 — reviewer pass applied: headline finding surfaced ("ranking
is not the bottleneck"; metadata labels miss 93–97% of true precedents; 29%
of relevant precedents pool-only), new title, abstract cut ~35%, intro
reordered problem-first, three TikZ figures (pipeline, temporal split,
metadata-vs-human with the real #38297/#37603 case), System section expanded
(indexing composition, token budgets, scoring formula, defaults vs frozen
protocol), new "Deployment Probe" section from eval/ab/RESULTS.md (12-task
paired A/B: dead heat p≈0.75, 2 convention transfers, 0 misled), "Why
merged-PR history?" discussion added, visionary closing sentence.

Deferred (needs new runs, tracked as planned follow-ups in Threats):
weight/field ablation; error taxonomy over the ~15 known-item misses.

Remaining before arXiv: author proof-read, references verification pass
(page numbers/volumes), one Overleaf compile (TikZ added — check figures),
and the endorsement step.

**Note on the abstract**: arXiv metadata caps abstracts at 1,920 characters;
the current abstract fits but is close to the limit — trim there, not in
the PDF, if the submission form complains.

**Rule carried over from EVAL.md**: every number in the paper must trace to a
hash-pinned manifest; negative results are reported as results.
