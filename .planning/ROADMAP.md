# Roadmap: Senrah

## Milestones

- ✅ **v1.0 GitHub-only MVP** — Phases 1–5 (shipped 2026-06-12) — [archive](milestones/v1.0-ROADMAP.md)
- ✅ **v1.1 Release Readiness** — Phases 6–8 (shipped 2026-06-18) — [archive](milestones/v1.1-ROADMAP.md)
- 📋 **v1.2 Corpus Depth** — Phases 9–11 (planning) — proving whether multi-year corpus depth is the retrieval lever, on a trustworthy temporal-holdout instrument

## Phases

<details>
<summary>✅ v1.0 GitHub-only MVP (Phases 1–5) — SHIPPED 2026-06-12</summary>

- [x] Phase 1: Walking Skeleton (4/4 plans) — completed 2026-05-31
- [x] Phase 2: MCP Server (3/3 plans) — completed 2026-06-01
- [x] Phase 3: Production Ingestion (5/5 plans) — completed 2026-06-08; gate #1 (resume data loss) root-fixed and live-validated 2026-06-10/12
- [x] Phase 4: Reindex & Config Tuning (direct execution) — completed 2026-06-12; live v1→v2 reindex of 575 rows
- [x] Phase 5: Observability & Hardening (direct execution) — completed 2026-06-12; QUAL-01..04 audited line-by-line (docs/QUAL-AUDIT.md)

Requirements: [milestones/v1.0-REQUIREMENTS.md](milestones/v1.0-REQUIREMENTS.md) — 31/31 complete
Full details: [milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md)

</details>

<details>
<summary>✅ v1.1 Release Readiness (Phases 6–8) — SHIPPED 2026-06-18</summary>

- [x] Phase 6: Continuous Integration (1/1 plan) — completed 2026-06-14; live Actions green after the flaky-embedder fix (`046684c`). (CI-01, CI-02, CI-03)
- [x] Phase 7: Release Pipeline (1/1 plan) — completed 2026-06-13 (`6cef68e`); live `senrah 0.1.0` published to TestPyPI from tag `v0.1.0` on 2026-06-18. (REL-01, REL-02, REL-03)
- [x] Phase 8: Documentation & Polish (2/2 plans) — completed 2026-06-14 (`e34782a`); FIX-01 regression-tested, 257 unit tests pass. (DOC-01..04, FIX-01)

Requirements: [milestones/v1.1-REQUIREMENTS.md](milestones/v1.1-REQUIREMENTS.md) — 11/11 complete
Full details: [milestones/v1.1-ROADMAP.md](milestones/v1.1-ROADMAP.md)

</details>

### 📋 v1.2 Corpus Depth (Phases 9–11)

- [x] **Phase 9: Eval v3 — Trustworthy Deduped Scale** — backport-cluster dedup, 19-miss triage, re-frozen known-item scale, blind judge calibration (the measuring stick, built and frozen *before* any depth measurement) -- completed 2026-06-24
- [ ] **Phase 10: Temporal-Holdout Harness + Multi-Year Ingest** — additive `corpus_t` window param, leak-free post-T query-set, automated hit-rate@k gate scorer with bootstrap CIs, full `--scope all` `dotnet/efcore` ingest + index
- [ ] **Phase 11: Depth Ladder + Judge + Decision Gate** — 3-rung ladder, recall@k guardrail per rung, conditional calibrated LLM-judge, plateau read-out, synchronized two-condition decision gate

## Phase Details

### Phase 9: Eval v3 — Trustworthy Deduped Scale
**Goal**: A deduped, triaged, re-frozen eval scale exists and is frozen — the measuring stick is trustworthy *before* any depth measurement runs.
**Depends on**: Nothing new (builds on the v1.0 frozen known-item eval and the raw diffs already in the DB)
**Requirements**: EVAL-01, EVAL-02, EVAL-03, EVAL-04, JUDGE-01
**Success Criteria** (what must be TRUE):
  1. A backport/cherry-pick cluster detector runs over the corpus from the stored raw diffs (zero GitHub re-fetch) and emits a connected-components cluster-map; a human-readable sample confirms it groups known backport pairs.
  2. The eval scoring path counts a hit on any cluster member as one cluster hit and counts distractors per-cluster, demonstrated on a fixture where per-PR vs per-cluster counts diverge.
  3. Each of the 19 known-item misses carries a recorded tag (real-fail / duplicate / label-error); duplicates are collapsed and label-errors corrected, with the triage decisions written down.
  4. A re-frozen deduped known-item scale exists with recomputed recall@1/@5/MRR and a recorded manifest, replacing the v1.0 frozen set as the baseline the depth experiment measures against.
  5. A ~50–100-pair human gold set and a reported Cohen's κ (judge-vs-human) exist, produced *blind* (before any depth result), with an explicitly agreed κ floor recorded; the same gold set also validates the EVAL-01 detector.
**Plans**: 4 plans
  - [x] 09-01-PLAN.md — EVAL-01: backport/cherry-pick cluster detector (union-find over corroborated edges, diff-sim never merges alone, under-merge bias) → frozen hash-pinned clusters.json [wave 1]
  - [x] 09-04-PLAN.md — JUDGE-01: blind judge calibration (stdlib Cohen's κ, stratified gold.jsonl, Sonnet 4.6→Opus 4.8 escalation ladder, eval extra + import-graph isolation guard) [wave 1, parallel]
  - [x] 09-02-PLAN.md — EVAL-02 + EVAL-03: reusable per-cluster grouping module (divergence fixture) + two-stage 19-miss triage (Stage-1 mechanical, Stage-2 human, no LLM) [wave 2]
  - [x] 09-03-PLAN.md — EVAL-04: re-frozen v3-knownitem-deduped manifest (cluster-sourced relevant_prs, recorded corrections, reused v2 query text) + run_eval re-run → results-v3-deduped.json [wave 3]

### Phase 10: Temporal-Holdout Harness + Multi-Year Ingest
**Goal**: The real, leak-free measurement instrument exists and the deep corpus is ingested — every depth rung is materializable from one ingest and one index.
**Depends on**: Phase 9 (the deduped frozen scale and cluster-map must exist before the deep corpus is measured)
**Requirements**: DEPTH-01, DEPTH-02, DEPTH-03, DEPTH-04
**Success Criteria** (what must be TRUE):
  1. The full multi-year `dotnet/efcore` history is ingested via `--scope all` into the existing raw store and indexed, with recorded row counts and no data loss (no rate-limit-throttle work — wall-clock time is accepted).
  2. `SkillRepo.search` accepts an additive, `None`-default `merged_at` corpus-window parameter; with it unset the search behaves exactly as before, and the MCP tool contract is unchanged (no version bump, connector untouched) — verifiable as the only `src/` change this milestone.
  3. A temporal-holdout split is defined with cutoff T chosen from the deep end (corpus strictly `merged_at < T`, query tasks strictly `merged_at > T`), with relevance labels derived from PR metadata only; an explicit leakage check confirms the split/label freeze on `merged_at` / original ingest timestamps, not current PR-body state.
  4. An automated temporal-holdout hit-rate@k scorer (coverage-at-threshold, applying the product `[BELOW THRESHOLD]` cutoff) is wired to the real `SkillRepo.search` path and produces a deterministic, reproducible number with bootstrap confidence intervals from a frozen index.
**Plans**: 5 plans
  - [x] 10-01-PLAN.md — Wave 0 test stubs: 4 failing-but-importable test files covering DEPTH-02/03/04 [wave 1]
  - [x] 10-02-PLAN.md — DEPTH-02: SkillRepo.search extension (merged_before/merged_after Optional[datetime] params) + unit + integration tests [wave 1, parallel]
  - [x] 10-03-PLAN.md — DEPTH-01 + DEPTH-03 (code): full --scope all ingest (efcore 487→8449) + index + clusters-deep.json (9594 prs, 397 multi-member) + eval/temporal/define_split.py [wave 2, checkpoint] — completed 2026-06-25
  - [ ] 10-04-PLAN.md — DEPTH-03 (gate) — ⚠ RE-SCOPED 2026-06-25: metadata-only answerable gives n=5/278 (underpowered). A-vs-B probe → label too narrow (verdict A). New leak-aware TREC-pooled judge-labeled relevance protocol (10-TEMPORAL-RELEVANCE-PROTOCOL.md); pulls JUDGE into Phase 10. [wave 3, checkpoint]
  - [ ] 10-05-PLAN.md — DEPTH-03/04 (instrument): bootstrap_ci.py + run_temporal_eval.py + green unit tests + baseline smoke run [wave 4] — scorer needs extension for judged labels

### Phase 11: Depth Ladder + Judge + Decision Gate
**Goal**: The depth experiment is run and a trustworthy decision-gate conclusion is recorded — depth is the lever (with a recommended ingest depth) or it is not (and BM25/connectors rise in priority).
**Depends on**: Phase 10 (requires the frozen scale from Phase 9, the `corpus_t` window param, the deep ingest, and the hit-rate@k scorer)
**Requirements**: DEPTH-05, DEPTH-06, DEPTH-07, JUDGE-02
**Success Criteria** (what must be TRUE):
  1. A reproducible hit-rate@k number with a bootstrap confidence interval exists for each of the ≥3 ladder rungs (3.5mo / 1yr / 2–3yr), with everything except corpus depth frozen (model+version, weights, threshold, k, query-set, scorer, cluster-set) and a per-rung reproducibility manifest recorded (corpus boundary, row counts, model/version, weights, k, cluster-set hash).
  2. The deduped known-item recall@k guardrail is re-run at each rung, producing per-rung ranking-quality numbers that show whether depth erodes ranking via added distractors.
  3. *(conditional — JUDGE-02)* If and only if the automated curve comes back flat, a calibrated LLM-judge (authority κ-gated by the Phase 9 floor) scores the unlinked convention-transfer hits per rung and records its own depth-curve read; the judge can add depth evidence but can never override the recall@k guardrail.
  4. A plateau read-out (hit-rate-vs-depth curve, plateau → recommended ingest depth) and a synchronized two-condition decision gate are recorded, yielding a documented "depth-is-the-lever (+ recommended depth)" or "depth-isn't-the-lever (BM25/connectors rise)" conclusion — where "flat" requires the automated curve flat AND the judge layer showing no depth-curve.
**Plans**: TBD

## Progress

| Phase | Milestone | Status | Completed |
|-------|-----------|--------|-----------|
| 1. Walking Skeleton | v1.0 | Complete | 2026-05-31 |
| 2. MCP Server | v1.0 | Complete | 2026-06-01 |
| 3. Production Ingestion | v1.0 | Complete | 2026-06-08 |
| 4. Reindex & Config Tuning | v1.0 | Complete | 2026-06-12 |
| 5. Observability & Hardening | v1.0 | Complete | 2026-06-12 |
| 6. Continuous Integration | v1.1 | Complete | 2026-06-14 |
| 7. Release Pipeline | v1.1 | Complete | 2026-06-18 |
| 8. Documentation & Polish | v1.1 | Complete | 2026-06-14 |
| 9. Eval v3 — Trustworthy Deduped Scale | v1.2 | Complete | 2026-06-24 |
| 10. Temporal-Holdout Harness + Multi-Year Ingest | 2/5 | In Progress|  |
| 11. Depth Ladder + Judge + Decision Gate | v1.2 | Not started | - |

---

*Roadmap created: 2026-05-30 · v1.0 archived: 2026-06-12 · v1.1 archived: 2026-06-18 · v1.2 scoped: 2026-06-22*
