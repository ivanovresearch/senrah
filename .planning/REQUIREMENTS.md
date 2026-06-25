# Requirements: Senrah — Milestone v1.2 Corpus Depth

**Milestone goal:** Determine whether, and by how much, multi-year corpus depth
improves precedent retrieval — measured on a trustworthy temporal-holdout
query-set, with a clean deduped eval scale built *first*. This is a measurement
milestone: the deliverable is a defensible decision-gate result (depth is the
lever, and the recommended ingest depth — or it is not, and BM25/connectors
rise in priority), not a new product feature.

REQ-IDs use fresh category prefixes for v1.2 (EVAL / DEPTH / JUDGE). Prior
milestones used STORE/INGEST/INDEX/SEARCH/MCP/OPS/QUAL (v1.0) and
CI/REL/DOC/FIX (v1.1), archived under `milestones/`.

**Sequence is a hard dependency, not a preference:** Eval v3 (EVAL-*) must land
and freeze before the depth experiment (DEPTH-*) runs — multi-year ingest
multiplies backports, so an un-deduped deep corpus makes the depth delta
uninterpretable.

**All eval/experiment machinery lives in `eval/`.** The only product-source
(`src/`) change this milestone is one additive, `None`-default `merged_at`
window parameter on `SkillRepo.search` (DEPTH-02); it is never exposed through
the MCP tool. Senrah ships no LLM client (the judge is a harness tool).

## v1.2 Requirements

### Eval v3 — Trustworthy Deduped Scale (EVAL)

*Prerequisite. Builds the measuring stick before any depth measurement.*

- [ ] **EVAL-01**: A backport/cherry-pick **cluster detector** over the corpus
  identifies PRs that are the same change, using cherry-pick provenance
  (`cherry picked from commit <SHA>`), backport title/label conventions
  (`[release/x] Backport of #N`), PR/issue cross-reference overlap, and
  near-identical diffs — computed from the raw diffs already stored in the DB
  (zero GitHub re-fetch) and collapsed via connected-components.
- [ ] **EVAL-02**: The eval scoring path performs **corpus-level cluster
  grouping** — a hit on any cluster member counts as one cluster hit, and
  distractors are counted per-cluster (not per-PR), keeping both the
  relevant-set and the distractor-set honest as corpus depth grows.
- [ ] **EVAL-03**: The 19 known-item misses are **triaged**, each tagged
  real retrieval-failure / duplicate / label-error; duplicates are collapsed
  and label-errors corrected so the scale measures signal, not artifact.
- [ ] **EVAL-04**: A **re-frozen deduped known-item scale** is produced
  (recall@1/@5/MRR recomputed on the deduped, triaged set) with a recorded
  manifest, serving as the trustworthy baseline the depth experiment measures
  against.

### Corpus Depth Experiment (DEPTH)

*The main experiment. Vary only corpus depth; read the curve shape.*

- [ ] **DEPTH-01**: A full multi-year `dotnet/efcore` history is ingested via
  `--scope all` into the existing raw store and index, with no data loss and no
  rate-limit throttle work (the 3.5-month ceiling was an assumption, not a
  blocker).
- [x] **DEPTH-02**: `SkillRepo.search` accepts an additive, `None`-default
  `merged_at` corpus-window parameter (corpus = PRs `merged_at < T [AND >=
  floor]`) that materializes every depth-ladder rung from the **one** ingest +
  **one** index; the parameter is never exposed through the MCP tool (no
  contract bump).
- [x] **DEPTH-03**: A **temporal-holdout split** is defined — cutoff T chosen
  from the deep end (2–3yr before T = deepest corpus rung; 1yr+ after T =
  query-set); corpus is strictly `merged_at < T`, query tasks strictly
  `merged_at > T`; the relevance label is derived from PR metadata only
  (leak-free — the answer text is never embedded). The leak-free property is
  backed by an **explicit leakage check**: the split and label freeze on
  `merged_at` / original ingest timestamps, **not** current PR-body state, so a
  description edited after T cannot retroactively contaminate the pre-T corpus
  (closes the "leak is the curve" risk).
- [x] **DEPTH-04**: An **automated temporal-holdout hit-rate@k scorer**
  (PRIMARY / GATE metric) measures **coverage-at-threshold** — did any relevant
  earlier precedent surface in top-k **above the product `[BELOW THRESHOLD]`
  cutoff** (not bare recall@k, which would measure the wrong axis) — wired to
  the real `SkillRepo.search` path so it applies the same threshold the product
  applies, deterministic and reproducible from a frozen index, with bootstrap
  confidence intervals per measurement.
- [ ] **DEPTH-05**: A **depth ladder** is run across ≥3 rungs (3.5mo baseline /
  1yr / 2–3yr) with everything except corpus depth frozen (embedding
  model+version, weights, threshold, k, query-set, scorer, and the cluster-set
  computed once on the deepest corpus), each rung recording a reproducibility
  manifest (corpus boundary, row counts, model/version, weights, k,
  cluster-set hash).
- [ ] **DEPTH-06**: The deduped known-item **recall@k guardrail** is re-run at
  each rung to confirm depth does not erode ranking quality via added
  distractors.
- [ ] **DEPTH-07**: A **plateau read-out and synchronized decision gate** is
  produced: hit-rate-vs-depth curve with the plateau (→ recommended ingest
  depth as a product setting); the gate is a literal two-condition check —
  "flat" requires the automated curve flat **AND** the judge layer (JUDGE-02,
  if admitted) showing no depth-curve in unlinked hits — yielding either "depth
  confirmed as the lever" or "depth flat → BM25/connectors rise in priority."

### LLM-Judge Secondary Layer (JUDGE)

*The judge is gate-synchronization machinery, not optional polish. Calibration
(JUDGE-01) is **committed and must be done blind** — building the gold set and
measuring κ *before* the depth result is known prevents a motivated judge
(calibrating to rescue a flat curve after seeing it). The full calibrated pass
over unlinked hits (JUDGE-02) stays **conditional/lazy** — triggered only if
the automated curve (DEPTH-04/07) comes back flat, since that is the only case
where the judge layer is needed to legitimately close "depth is not the lever."
Lives entirely in `eval/`; `anthropic` is added in a `[project.optional-
dependencies] eval` extra so `pip install senrah` stays LLM-free.*

- [x] **JUDGE-01** *(committed)*: A judge **calibration** step builds a
  ~50–100-pair human gold set and reports **Cohen's κ** (judge-vs-human,
  chance-adjusted) **before** any depth result is known; below an explicitly
  agreed κ floor the judge is advisory-only and cannot open or close the depth
  gate. The same gold set also validates the EVAL-01 backport-cluster detector.
- [ ] **JUDGE-02** *(conditional)*: A calibrated **LLM-judge secondary layer**
  scores the *unlinked* convention-transfer relevance the automated label
  misses, per rung, with κ-gated authority feeding the DEPTH-07 decision gate;
  run only if the automated curve is flat. It can add evidence of depth value
  but can never override the recall@k guardrail.

## Future Requirements (deferred past v1.2)

- Retrieval quality: BM25 / hybrid lexical retrieval, diff summary — priority
  rises only if the v1.2 gate shows depth flat on a clean deduped corpus.
- New connector (GitLab / Bitbucket / local git) via the existing seam —
  breadth is an orthogonal axis, pursued after depth is proven.
- Diff chunking / re-embedding to chase truncated misses (19% truncate at 6000
  tokens) — reindex-only future work; surfaces here only as a stratification
  signal if misses concentrate in truncated targets.
- T-sensitivity (1–2 alternate cutoffs) and a 4th interpolating ladder rung —
  do only if the headline v1.2 result is borderline.
- DB-resident cluster grouping (a `cluster_id` column + migration 0004) — only
  if Design A (clusters computed in `eval/`, no migration) proves insufficient.
- Production PyPI release (carried from v1.1; pipeline + TestPyPI already proven).

## Out of Scope (v1.2)

- **Product-side / search-time dedup** (collapsing backports in MCP results) —
  dedup here is an eval-set / corpus-grouping concern; changing what
  `search_prs_v1` returns would alter the system under test and break the
  versioned MCP contract.
- **Rate-limit proactive throttle** — explicitly out; the full `--scope all`
  ingest runs as-is and is allowed to take wall-clock time.
- **LLM providers inside senrah** — charter invariant; senrah is read-only
  search and the judge is a harness tool in `eval/` only.
- **Weight / threshold tuning during the ladder** — the A/B already showed
  tuning is second-order to depth; tuning mid-experiment is an uncontrolled
  variable. Weights (0.6/0.4), threshold, and k are frozen for every rung.
- **A full TREC-style multi-grade human qrel set** — over-engineered for a
  single coverage question; the automated leak-free label + the ~50–100-pair
  judge-calibration gold set is sufficient.

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| EVAL-01 | Phase 9 | Pending |
| EVAL-02 | Phase 9 | Pending |
| EVAL-03 | Phase 9 | Pending |
| EVAL-04 | Phase 9 | Pending |
| DEPTH-01 | Phase 10 | Pending |
| DEPTH-02 | Phase 10 | Complete |
| DEPTH-03 | Phase 10 | Complete |
| DEPTH-04 | Phase 10 | Complete |
| DEPTH-05 | Phase 11 | Pending |
| DEPTH-06 | Phase 11 | Pending |
| DEPTH-07 | Phase 11 | Pending |
| JUDGE-01 | Phase 9 | Complete |
| JUDGE-02 | Phase 11 | Pending (conditional) |

**Total v1.2 requirements:** 13 (12 committed + 1 conditional judge-layer pass)
**Mapped:** 13/13 ✓ (Phase 9: 5 · Phase 10: 4 · Phase 11: 4)
