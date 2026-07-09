# How Senrah Measures Retrieval Quality

This document is the honest account of how I evaluate whether Senrah actually
does its one job — surface the *most relevant* merged-PR precedent for a task —
and what I have and have not proven. It is written to be read by someone who
evaluates evaluations for a living. Where a result is strong I say so; where it
is inconclusive or negative I say that too, with the number.

The short version: retrieval is easy to demo and hard to measure. Most of the
engineering effort here went into the measurement, not the retrieval.

---

## TL;DR

- **Known-item retrieval works well and is frozen.** On a deduped `dotnet/efcore`
  corpus, recall@5 = **0.90**, MRR@10 = **0.79** over 218 held-out queries.
- **That number is not enough**, and I refuse to sell it as if it were. Known-item
  measures *ranking quality*, not *forward coverage* — the harder, leak-free
  question of whether a task can find a precedent that genuinely predates it.
- I built a **leak-free temporal-holdout harness** to answer the harder question,
  discovered my first relevance label was **underpowered**, diagnosed it with a
  **labeled A/B probe**, and rebuilt relevance as a **TREC-pooled, human-calibrated**
  definition.
- The LLM judge intended to scale that labeling **failed its calibration gate**
  (Cohen's κ = 0.39 vs a pre-registered 0.6 floor). I record it as a negative
  result and keep the judge advisory-only rather than launder a number through it.

Everything below is the long version.

---

## 1. Why measurement is the hard part

A retrieval tool is trivial to make *look* good: pick a few queries where it
returns something plausible, screenshot it, ship. The failure mode of that
approach is that you never learn whether the tool works — you learn whether you
can cherry-pick.

Senrah's core promise is ranked semantic retrieval, so the honest questions are:

1. **Ranking quality** — when the answer is known to exist, does it rank near the top?
2. **Forward coverage** — for a *new* task, does a genuinely-relevant *prior*
   precedent exist and get retrieved, with no leakage from the future?

These are different questions. (1) is necessary but not sufficient. (2) is the
one that decides whether the product is actually useful, and it is the one that
is easy to get subtly, invalidatingly wrong.

---

## 2. Instrument 1 — known-item retrieval (frozen, reproducible)

**Definition.** For a known issue whose fixing PR is known, does that PR appear
in the top-k results for the issue text? This is the standard known-item / TREC
"find the target" setup.

**Corpus & protocol.** `dotnet/efcore`, deduped: 575 merged PRs (merged
2024-04-06 … 2026-06-12), 218 held-out queries. Problem/solution embedding
weights 0.7/0.3. Manifest is hash-pinned; the run is deterministic.

| Metric    | Value |
|-----------|-------|
| recall@1  | 0.71  |
| recall@5  | 0.90  |
| recall@10 | 0.93  |
| MRR@10    | 0.79  |

**Dedup matters, and it is not free.** A naive corpus double-counts
backports/cherry-picks, which inflates recall (the "same" fix appears several
times, so *a* hit is easy). I detect backport clusters from the stored diffs
(union-find over corroborated edges; diff-similarity alone is never allowed to
merge two PRs), and I score **per-cluster**: a hit on any cluster member counts
once, and distractors are counted per-cluster too. The divergence between per-PR
and per-cluster scoring is demonstrated on a fixture where the two disagree.

**Why this is not the whole story.** Known-item recall answers "can it rank a
target it is *told* exists." It says nothing about coverage for genuinely new
work, and — critically — on a *deeper* corpus known-item recall can **fall**,
because more PRs means more near-duplicate distractors competing for the top-k
slots. That is exactly why a second instrument is required.

---

## 3. Instrument 2 — leak-free temporal-holdout

**The question.** Pick a cutoff time T. Treat everything merged *before* T as the
retrievable corpus, and everything merged *after* T as incoming tasks. For each
post-T task, is there a relevant precedent strictly before T, and does Senrah
retrieve it? This simulates the real usage: an agent working today, retrieving
from history.

**The leakage checks (this is where temporal evals die).**
- Corpus is filtered strictly `merged_at < T`; queries strictly `merged_at > T`.
  The split freezes on `merged_at` and the original ingest timestamps, **not** on
  current PR-body state (which can be edited after the fact).
- Relevance labels and the corpus boundary are frozen together, so a later
  re-ingest cannot silently move the boundary.
- The scorer applies the *product's* `[BELOW THRESHOLD]` cutoff, so the eval
  measures what a user actually gets, not an idealized ranking.

The full multi-year history was ingested for this (`efcore` 487 → **8449** PRs,
one ingest + one index, every depth rung materializable from it) and a deep
cluster map built (9594 PRs, 397 multi-member clusters, hash-pinned).

---

## 4. The failure I did not hide — statistical power

With relevance defined the obvious way (a task is "answerable" iff a metadata-
linked prior PR exists), the harness came back **underpowered**:

| Cutoff T (days back) | Post-T tasks w/ issue | Answerable (relevant precedent strictly pre-T) |
|----------------------|-----------------------|------------------------------------------------|
| 365                  | 278                   | **5**                                          |
| 455                  | 306                   | 4                                              |
| 545                  | 345                   | 4                                              |

The pre-registered power floor was **≥ 80** answerable queries. Five is not a
measurement; it is noise. The intellectually cheap move here is to quietly pivot
the whole phase to the known-item number that already looks good and never
mention it. I did not do that, because `n=5` has **two opposite explanations**
and they lead to opposite conclusions:

- **A — the label is too strict.** Metadata-linked relevance misses precedents
  that were transferred by convention rather than by an explicit issue link.
  Then the instrument is *rescuable* with a better relevance definition.
- **B — precedents are genuinely rare.** `n≈5` is real, and the honest output is
  a *negative* result: forward coverage is structurally small.

You cannot tell A from B by staring at the number, and pivoting to known-item
would have *hidden* the distinction. So I ran a probe.

---

## 5. Diagnosis — a labeled A/B probe

I drew 30 random post-T(365) tasks and hand-checked whether a genuine relevant
precedent existed strictly before T, independent of whether metadata linked it.

**Result:** a genuine pre-T precedent existed for **≥ 14/30** (strict reading) up
to **~29/30** (lenient reading). The metadata label was undercounting true
relevance by roughly **14–29×**.

**Verdict: A.** The label was too narrow; the temporal instrument is rescuable
with a leak-free, wider relevance definition. Not a pivot to known-item.

This is the whole point of the exercise: the first result was not the finding —
it was a *symptom*, and the probe told me which disease it was.

---

## 6. Rebuilding relevance — TREC-style pooling

If metadata links undercount relevance, I need relevance judged, not inferred.
Judging every (task, candidate) pair is infeasible, so I use **TREC pooling**:
for each task, union the top candidates from independent retrieval systems into
a judgment pool, then judge the pool.

**Pool construction (decision D1): union of legs.**
- `eval` — the evaluated production embedding retriever.
- `bm25_unique` — candidates only lexical BM25 (over enriched text) surfaces.
- `bge_unique` — candidates only a second-family embedder (bge-m3) surfaces.

Each leg is included because each contributes relevant precedents the others
miss. Concretely, a BM25-only pool recalled just **72%** of judged-relevant
candidates — the embedding legs contribute the semantic-only ~28%. Dropping any
leg biases the pool. (Independence lives in the *judgment*, not in the pool — the
pool is deliberately a union.)

---

## 7. Human calibration — the gold set

I hand-labeled a blind gold set **before** looking at any depth result: 270
(task, candidate) pairs = 30 tasks × 9 candidates (3 from each pool leg),
labeled yes/no for genuine relevance.

**Density:** 65 yes / 205 no → **24%** positive.

**Coverage — the power failure is resolved.** **29 of 30** tasks are answerable
under judged relevance (were `5` under the metadata label). Verdict A confirmed
by hand: the instrument is not structurally underpowered; the old label was.

**Per-leg precision — the pool design is validated.**

| Leg           | relevant / total | precision |
|---------------|------------------|-----------|
| `eval`        | 46 / 90          | 0.51      |
| `bge_unique`  | 11 / 90          | 0.12      |
| `bm25_unique` | 8 / 90           | 0.09      |

The main embedding leg is precise (every second candidate is relevant). The
unique legs are low-precision but **non-zero**: together they contribute **19 of
65** relevant precedents (**29%**) that the main leg missed — the classic TREC
pooling trade (coverage bought at the cost of precision). This is direct evidence
that the union pool earns its cost and neither BM25 nor bge can be dropped.

---

## 8. The judge — a calibration gate it did not pass

Hand-labeling 30 tasks rescued power, but the instrument needs ~80+ tasks to
clear its floor, and hand-labeling ~720 pairs per corpus does not scale. The plan
was an LLM judge to label the full pool — **conditional on it passing a
pre-registered authority gate**: Cohen's κ (judge vs human) ≥ **0.6**.

It did not pass.

| Judge (framing)              | κ vs human | Verdict          |
|------------------------------|-----------|------------------|
| Sonnet 4.6 (Phase-9 gold)    | 0.27      | below floor      |
| Opus 4.8 (Phase-9 gold)      | 0.39      | below floor      |
| Sonnet 4.6 (temporal gold)   | **0.39**  | **below floor**  |

On the temporal gold the confusion matrix (human × judge) is TP=23, FN=42, FP=7,
TN=198 → **precision 0.77, recall 0.35**. The judge misses **65%** of
human-relevant precedents, and the misses are concentrated exactly where it
hurts: **10/11** `bge_unique` and **6/8** `bm25_unique` relevant precedents — the
semantic / convention-transfer cases the pool was widened to capture. The judge's
blind spot is aligned with the pool's whole reason for existing.

**Decision:** the judge stays **advisory-only**. It can add evidence but can
never override the recall@k guardrail. I would rather report a slower,
human-anchored result than launder coverage through a labeler I have measured to
be unreliable on the cases that matter. Its yes-labels are trustworthy
(precision 0.77); its no-labels are not (recall 0.35), and I treat it
accordingly.

This is a negative result, recorded as one. It constrains the design: scaling the
temporal instrument to full power needs either more human labeling or a
materially better judge — not a pretend one.

---

## 9. Status — what is proven and what is open

**Proven.**
- Known-item ranking on `efcore`: recall@5 = 0.90, frozen and reproducible.
- Backport-dedup is real and measurable (per-cluster scoring, hash-pinned).
- A leak-free temporal-holdout harness exists and its leakage assumptions are
  checked, not assumed.
- Relevance is human-anchored on a blind gold set; the union pool is validated
  per-leg.

**Open (tracked, not swept under the rug).**
- The full-power temporal depth number is not yet reported: the scalable labeler
  (LLM judge) failed calibration, so the depth-vs-coverage decision gate is still
  human-labeling-bound.
- Everything is validated on a single corpus (`efcore`); the connector seam is
  built for more sources but not yet exercised on a second one.

---

## 10. Principles I held

- **The first number is a symptom, not a finding.** `n=5` was not the answer; the
  probe that explained it was.
- **Pre-register the gate.** The κ ≥ 0.6 floor and the power floor of 80 were
  fixed before the results, so a disappointing result could not be redefined into
  a passing one.
- **Blind before you look.** The human gold was labeled before any depth result,
  so the labels could not be rationalized toward a desired outcome.
- **Negative results are results.** A judge that fails calibration and a coverage
  question that is still open are reported plainly, with the numbers, because a
  measurement you cannot trust is worse than no measurement.
