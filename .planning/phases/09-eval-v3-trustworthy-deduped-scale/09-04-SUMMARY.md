---
phase: 09-eval-v3-trustworthy-deduped-scale
plan: "04"
subsystem: eval/judge
tags: [judge-calibration, cohen-kappa, gold-set, eval-extra, import-guard]
dependency_graph:
  requires: []
  provides: [JUDGE-01]
  affects: [pyproject.toml, .env.example]
tech_stack:
  added:
    - "anthropic>=0.111.0,<1 (eval optional-dep only)"
  patterns:
    - "Pure stdlib Cohen's kappa via collections.Counter"
    - "Optional dependency extra isolation (D-17)"
    - "Monkeypatch-compatible grade_fn via sys.modules lookup"
    - "Binary collapse: related+direct-precedent -> relevant, only for kappa"
key_files:
  created:
    - eval/judge/__init__.py
    - eval/judge/kappa.py
    - eval/judge/judge.py
    - eval/judge/gold.jsonl
    - tests/unit/test_kappa.py
    - tests/unit/test_judge_isolation.py
    - eval/__init__.py
  modified:
    - pyproject.toml
    - .env.example
decisions:
  - "Binary collapse frozenset includes 'relevant' (already-binary) to avoid double-collapse bug"
  - "grade_fn resolved via sys.modules at call time to support monkeypatch in tests"
  - "gold.jsonl uses 60 rows across 4 strata: 14 backport, 19 clear-relevant, 7 clear-irrelevant, 20 hard"
metrics:
  duration: "~45 minutes"
  completed: "2026-06-23"
  tasks: 2
  files: 9
---

# Phase 09 Plan 04: JUDGE-01 Blind Judge Calibration — Summary

Built the JUDGE-01 blind judge-calibration harness in `eval/judge/`: a pure stdlib Cohen's kappa implementation, a stratified 60-pair gold set doubling as EVAL-01 detector validation, the 3-grade anthropic judge with Sonnet 4.6 -> Opus 4.8 escalation ladder, and the packaging isolation guard keeping `pip install senrah` LLM-free.

## What Was Built

### Cohen's kappa (`eval/judge/kappa.py`)
Pure stdlib (`collections.Counter`), no scipy. Accepts raw 3-grade or already-binary pairs; binary collapse (`related`+`direct-precedent` -> `relevant`) applied internally. Edge case: if all pairs are in one category (`p_e >= 1.0`), returns 1.0. Key implementation detail: the `_RELEVANT` frozenset includes `"relevant"` itself so already-binary test inputs are not collapsed to "irrelevant".

### 3-grade judge harness (`eval/judge/judge.py`)
`grade_pair(query, candidate_problem, candidate_diff, model)` calls the Anthropic sync API with temperature=0, parses `GRADE: <grade>` from the final response line, and preserves the raw 3-grade. `calibrate(gold, api_key)` runs the Sonnet 4.6 -> Opus 4.8 escalation ladder: Sonnet scores the gold set, kappa is computed (binary-collapsed), Opus is invoked only if kappa < 0.6. Both kappa values are recorded. Advisory-only verdict emitted if even Opus < 0.6.

`anthropic` is imported lazily inside `grade_pair` (not at module level) so the module can be imported in tests without `pip install senrah[eval]`. For testability, `_score_gold` resolves `grade_pair` via `sys.modules` at call time, enabling monkeypatching.

### Stratified gold set (`eval/judge/gold.jsonl`)
60 rows, all strata covered:
- **backport (14)**: Known backport pairs from dotnet/efcore history; [release/X.Y] prefix + identical fix body. Labeled `direct-precedent`. These validate EVAL-01 clustering (backport pairs should cluster together AND be graded as direct-precedent).
- **clear-relevant (19)**: Same feature/area, high-precision match.
- **clear-irrelevant (7)**: Different provider or completely unrelated problem.
- **hard (20)**: Ambiguous cases requiring judgment (similar fix class but different specialization).

Gold set drawn BLIND — produced before any depth result is known. No `ANTHROPIC_API_KEY` or other secrets in the file.

### Package isolation (`pyproject.toml` + `.env.example`)
`eval = ["anthropic>=0.111.0,<1"]` added as a NEW sibling group under `[project.optional-dependencies]` — NOT in `dependencies`, NOT in `dev`. `pip install senrah` stays LLM-free. `ANTHROPIC_API_KEY=sk-ant-placeholder_...` added to `.env.example` (placeholder, no real value).

### Import-graph guard (`tests/unit/test_judge_isolation.py`)
Scans all `.py` files under `src/senrah/` and asserts no import line contains `anthropic`. Self-test verifies the guard would correctly detect a hypothetical import. Modeled on `tests/unit/test_scoring.py::TestScoringModuleConstraints`.

## Verification

```
pytest tests/unit/test_kappa.py tests/unit/test_judge_isolation.py -q
# 15 passed
```

- `TestCohensKappaFormula`: 7 hand-computed cases (perfect→1.0, systematic disagreement→0.0, chance→0.2, known 2x2→0.70, high agreement→0.794, float type, 3-grade binary collapse)
- `TestKappaModuleConstraints`: stdlib-only import scan
- `TestEscalationLadderStub`: 3 tests asserting Opus iff Sonnet kappa < 0.6, raw 3-grade preserved, binary collapse only for kappa
- `TestSenrahImportGraph`: 3 import-isolation tests

Live calibration run (one-time, not CI): requires `ANTHROPIC_API_KEY` + `pip install senrah[eval]`; run with `python eval/judge/judge.py`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _binary_collapse frozenset missing "relevant" key**
- **Found during:** Task 1 GREEN phase
- **Issue:** `_RELEVANT = frozenset(("related", "direct-precedent"))` — the word "relevant" (already-binary form) was absent. Tests that passed already-binary pairs like `("relevant", "relevant")` had them collapsed to "irrelevant", making all pairs agree -> kappa = 1.0 for all inputs.
- **Fix:** Added `"relevant"` to the frozenset: `frozenset(("related", "direct-precedent", "relevant"))`.
- **Files modified:** `eval/judge/kappa.py`
- **Commit:** 274f7f3

**2. [Rule 1 - Bug] Test stub signatures mismatched grade_pair API**
- **Found during:** Task 1 escalation ladder test GREEN phase
- **Issue:** Test stubs used `(query, candidate, model)` but `grade_pair` is defined as `(query, candidate_problem, candidate_diff, model)` — 4 parameters. The monkeypatched stubs received unexpected keyword arguments, causing TypeError.
- **Fix:** Updated all three escalation test stubs to use the 4-parameter signature `(query, candidate_problem, candidate_diff, model)`.
- **Files modified:** `tests/unit/test_kappa.py`
- **Commit:** 274f7f3

## Known Stubs

None — `calibrate()` and `grade_pair()` are fully implemented with real API calls. The live calibration is a one-time run (not CI), not a stub. The import of `anthropic` is deferred to `grade_pair` to allow import without the eval extra installed, but this is an intentional design choice, not a stub.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| T-09-01 (mitigated) | .env.example | ANTHROPIC_API_KEY added as placeholder only; no real value |
| T-09-04 (mitigated) | pyproject.toml, tests/unit/test_judge_isolation.py | anthropic confined to eval extra; import-graph guard CI-testable |
| T-09-05 (mitigated) | eval/judge/judge.py, eval/judge/gold.jsonl | Calibration is blind (gold set drawn before depth measurement); kappa-gated; both Sonnet/Opus kappa recorded |

## Self-Check: PASSED

Files verified:
- FOUND: eval/judge/kappa.py
- FOUND: eval/judge/judge.py
- FOUND: eval/judge/gold.jsonl
- FOUND: tests/unit/test_kappa.py
- FOUND: tests/unit/test_judge_isolation.py

Commits verified:
- FOUND: 5c00e23 (RED phase tests)
- FOUND: 274f7f3 (GREEN phase: kappa, judge, extras, isolation)
- FOUND: 6a4b651 (gold.jsonl)

Full test run: 302 unit tests passed (no regressions).
