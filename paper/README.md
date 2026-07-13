# Paper: Leak-Free Temporal Evaluation of Precedent Retrieval

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

**Status**: v0.1 — structure + abstract + frozen numbers. All `TODO(v0.2)`
sections have their source material ready in docs/EVAL.md; the writing task
is transposition and related-work coverage, not new analysis.

**Rule carried over from EVAL.md**: every number in the paper must trace to a
hash-pinned manifest; negative results are reported as results.
