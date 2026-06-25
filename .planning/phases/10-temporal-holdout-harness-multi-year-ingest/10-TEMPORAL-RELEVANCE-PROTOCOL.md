# Temporal-Holdout Relevance Protocol (leak-aware redesign) — FOR REVIEW

**Status:** DRAFT for review — nothing runs until approved.
**Date:** 2026-06-25
**Why:** The metadata-only `answerable` label (linked-issue OR backport-cluster, strictly pre-T)
yields n_answerable = 5 / 278. The A-vs-B probe (30-sample, top-10 pre-T candidates via the real
search) found a genuine pre-T precedent for **≥14/30 (strict)** … **~29/30 (lenient)** tasks —
the label undercounts by ~14–29×. Verdict **A**: the label is too narrow (it cannot see "different
issue, same solution pattern" = unlinked convention-transfer), NOT that precedents are rare.
Temporal-holdout is rescuable; the rescue is a wider, **leak-free** relevance label.

This protocol resolves the three things the menu could not (judge re-calibration, frozen-label /
measure-on-top, depth-neutral pooling). It is option-2 (independent candidate pool → content judge)
inside option-4 (review-before-launch).

---

## 0. Non-negotiable invariants

- **I1 — Independence is in the JUDGMENT, not the pool (TREC-pooling stance, chosen 2026-06-25):**
  relevance is decided by a content judge reading task+candidate, NEVER from the embedding's
  rank/score. The evaluated embedding MAY contribute candidates to the pool (standard TREC pooling),
  because a candidate it ranks #1 can still be judged NOT-relevant → the embedding cannot self-credit.
  What is forbidden: "top embedding result = relevant" (score-derived labels = circularity).
  Rationale for moving off a pure-independent (BM25-only) pool: the §6.2 pre-launch check showed
  BM25 (even enriched, M=100) recalls only ~21/29 (72%) of judged-genuine precedents — ~28% are
  semantic-only matches lexically invisible to BM25. A BM25-only pool would structurally drop those
  from the label, biasing the measurement against the embedding's strength. TREC union fixes recall;
  judgment-level independence preserves leak-freeness.
- **I2 — Frozen-once:** relevance labels are computed ONCE against the deepest corpus and frozen.
  Every depth rung measures the SAME labeled set; the embedding is applied ONLY at measure time.
- **I3 — Depth-neutral pooling:** the BM25 pool component must not artifactually correlate with
  corpus depth on the 12-yr history. (The embedding component IS the evaluated system; its depth
  behavior is the signal, not an artifact. Pool+labels frozen on deepest corpus ⇒ no per-rung pool
  drift ⇒ TREC pool-bias does not leak into the depth curve.)

---

## 1. Pipeline overview

```
Task set (post-T tasks, frozen issue-text query)
   │
   ▼  Step 1: independent (non-embedding) candidate POOL per task   ── frozen on deepest corpus (I3)
   │            BM25 over PR problem text, top-M, pre-T only
   ▼  Step 2: content JUDGE labels each (task, candidate): genuine precedent? yes/no   (I1)
   │            κ RE-CALIBRATED in THIS framing on a NEW blind gold (a)
   ▼  Step 3: FREEZE relevance labels (task → set of relevant pre-T PR#) + manifest   (I2)
   │
   ▼  Step 4: MEASURE hit-rate@k per depth rung with the evaluated EMBEDDING on top of frozen labels
                denominator fixed = tasks answerable-at-deepest; bootstrap CI
```

---

## 2. Step 1 — TREC-pooled candidate pool (resolves I1-as-judgment + I3 / point c)

- **Pool = union of two retrievers on the deepest pre-T corpus, top-M each:**
  - **BM25** over enriched PR text (title+body+files+capped-diff, CamelCase-split). Lexical leg.
  - **Evaluated embedding** (`SkillRepo.search`, merged_before=T). Semantic leg — contributes the
    ~28% semantic-only precedents BM25 misses. Allowed in the pool (TREC), not in the label.
  - (shared-files / area rejected as a pool leg: file-touch counts grow with age ⇒ depth-correlated.)
- **M:** start M=50 per leg (union ≤100/task). §6.2 recall on the 30 probe tasks must clear a target
  (BM25 leg alone ≈72% @100; union with the embedding leg should approach ~100% since the probe
  precedents were embedding-surfaced — verify, don't assume).
- **Frozen once (I2):** pool computed once on the deepest corpus; rungs do NOT re-pool, only restrict
  the searchable window at measure time. ⇒ no per-rung pool drift, TREC pool-bias cannot enter the curve.
- **Mandatory pre-launch checks (§6):** (1) BM25-leg rank does not correlate with candidate `merged_at`;
  (2) judged-relevant `merged_at` not skewed to one end; (3) union pool recall of probe precedents.

## 3. Step 2 — Content judge + κ re-calibration (resolves point a)

- Judge reads **task issue-text + candidate problem+diff**, outputs `genuine_precedent: yes/no`
  with a 1-line rationale. It never sees embedding scores or ranks (I1).
- **κ is RE-MEASURED in THIS framing — Phase-9's verdict is NOT inherited.** Phase 9 calibrated the
  judge on known-item *relevance grading*; "genuine precedent yes/no" is a different task.
  - Draw a NEW **blind** gold of ~60–100 (task, candidate) pairs, stratified across BM25 score bands
    and candidate age; **you** label them yes/no BEFORE seeing judge output.
  - Run judge on the same pairs → Cohen's κ (judge vs human) in this framing, recorded with provenance.
- **Authority gate (Phase-9 floor):**
  - κ ≥ floor → judge may label the full pool autonomously.
  - **κ < floor → answerable is defined CONSERVATIVELY, NOT by judge alone** — choose at review:
    - (i) **intersection** `judge_yes ∧ metadata-proxy_yes` (high precision, lower recall), or
    - (ii) **manual** human labeling of a smaller FIXED query subset (e.g., 100 tasks) — judge advisory only.
- A task is **answerable** iff ≥1 pooled candidate is labeled a genuine precedent (under the rule above).

## 4. Step 3 — Freeze (resolves I2)

- Compute labels ONCE on the deepest corpus → freeze:
  - `query-set-judged.json`: tasks + per-pair labels + `relevant_pre_T = {PR#…}` + `is_answerable`.
  - `gold-precedent.jsonl`: the blind human gold.
  - `judge-calibration-temporal.json`: κ (this framing), floor, decision (authoritative | fallback-i | fallback-ii).
  - `manifest-temporal.json` (extended): pool signal+params (BM25, M), judge model+version, κ,
    label-freeze hash, frozen experiment params (score_threshold=0.45, k, weights, bootstrap seed/B).
- After freeze the label set is immutable; re-runs reuse it (reproducible).

## 5. Step 4 — Measure depth curve (resolves I2, comparability)

- For each rung d ∈ {3.5mo, 1yr, 2–3yr}: embed task query, `SkillRepo.search(merged_before=T,
  merged_after=T−d, repos=[efcore], score_threshold=0.45, k)`; **HIT iff any top-k result ∈
  frozen `relevant_pre_T` for that task**.
- **Denominator = tasks answerable-at-deepest (FIXED across rungs).** Shallow rungs naturally score
  lower because some relevant precedents fall outside [T−d, T) — that drop IS the depth signal.
- Bootstrap CI (seed=42, B=2000) over the fixed answerable set. Power is now driven by the widened
  answerable N (probe implies O(100+), pending judge), not 5.

## 6. Pre-launch depth-neutrality + sanity checks (point c — SHOW before running)

1. **Pool/age correlation:** Spearman(BM25 rank, candidate `merged_at`) ≈ 0; histogram of judged-
   relevant precedent `merged_at` roughly tracks corpus density, not skewed to one end.
2. **Pool recall sanity:** on the 30 probe tasks, the BM25 pool top-20 should contain the precedents
   I judged YES (else pooling misses true precedents → widen M or add a secondary signal).
3. **κ recorded** before any depth number is read (blind, like Phase 9).
4. **Leakage re-check:** query text frozen (linked-issue text), labels frozen on metadata/content,
   never re-fetched per rung.

---

## 7. Scope / process flags (eyes-open)

- This **pulls the calibrated judge into Phase 10** as REQUIRED for the instrument — in the original
  plan the judge (JUDGE-02) was Phase 11's *conditional* layer. That is a real scope change to
  DEPTH-03's relevance definition; record as a phase decision (not a silent rescope).
- `define_split.py` / `run_temporal_eval.py` (already written) need extension: BM25 pooling + judged-
  label ingestion + answerable-at-deepest denominator. The metadata-only path stays as a recorded
  baseline (n=5) for contrast, not as the gate.
- **Do NOT** promote known-item recall@k to primary depth instrument — it measures ranking quality,
  falls from distractors at depth, and reversing the phase's metric separation would hide A.

## 8. Open decisions for review (before any run)

| # | Decision | Resolution |
|---|----------|------------------|
| D1 | Pool signal | **TREC union: BM25(enriched) ∪ evaluated-embedding**, top-M each (DECIDED 2026-06-25) |
| D2 | Pool size M | 50/leg (≤100/task); validated by §6.2 union-recall on the 30 probe tasks |
| D3 | Gold size / labeler | ~60–100 pairs, blind, human (you) |
| D4 | κ<floor fallback | **decide by fact** — compute κ first, then pick (judge∩proxy vs manual fixed subset) (DECIDED 2026-06-25) |
| D5 | Query set / T | all post-T(T=365) tasks (278); revisit if N too low |
| D6 | Depth denominator | answerable-at-deepest (fixed) |
| D7 | Judge model | reuse Phase-9 ladder (Sonnet→Opus escalation), eval-extra isolated |
