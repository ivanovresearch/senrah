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

**Status**: v0.2 — full draft: all sections written, references.bib added
(14 established references; verify page numbers/volumes once against the
originals before submission). Remaining before arXiv:
author proof-read, one Overleaf compile pass, and the endorsement step.

**Note on the abstract**: arXiv metadata caps abstracts at 1,920 characters;
the current abstract fits but is close to the limit — trim there, not in
the PDF, if the submission form complains.

**Rule carried over from EVAL.md**: every number in the paper must trace to a
hash-pinned manifest; negative results are reported as results.
