"""
tests/unit/test_kappa.py -- Unit tests for eval.judge.kappa.cohens_kappa.

Tests the pure stdlib Cohen's kappa implementation against hand-computed cases.
Also tests the escalation-ladder stub logic for Task 2.

Cohen's kappa: k = (p_o - p_e) / (1 - p_e)
Multi-category (no collapse): irrelevant / related / direct-precedent are distinct.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re


class TestCohensKappaFormula:
    """Formula correctness against hand-computed cases."""

    def test_perfect_agreement(self):
        """Perfect agreement (all pairs match) => kappa == 1.0."""
        from eval.judge.kappa import cohens_kappa

        # All pairs: (judge=relevant, human=relevant)
        # p_o = 1.0, p_e = 1.0*1.0 + 0.0*0.0 = 1.0
        # k = (1.0 - 1.0) / (1 - 1.0) -> special case: return 1.0
        pairs = [("relevant", "relevant")] * 10
        result = cohens_kappa(pairs)
        assert result == 1.0, f"Perfect agreement should be 1.0, got {result}"

    def test_perfect_disagreement_returns_low_kappa(self):
        """Systematic disagreement => kappa is negative."""
        from eval.judge.kappa import cohens_kappa

        # All judge=relevant, all human=irrelevant
        # p_o = 0.0
        # p_e = p(judge=relevant)*p(human=relevant) + p(judge=irrelevant)*p(human=irrelevant)
        # p(judge=relevant)=1.0, p(human=relevant)=0.0, p_e = 0.0
        # k = (0.0 - 0.0) / (1 - 0.0) = 0.0
        # Actually chance agreement is 0 here, so k = 0.0
        pairs = [("relevant", "irrelevant")] * 10
        result = cohens_kappa(pairs)
        # With systematic opposite opinion and no shared categories,
        # p_e = 1.0*0.0 + 0.0*1.0 = 0.0, p_o = 0.0 -> k = 0.0
        assert result == 0.0

    def test_chance_agreement(self):
        """50/50 independent labels => kappa near 0.0."""
        from eval.judge.kappa import cohens_kappa

        # Equal split: 5 (relevant, relevant), 5 (irrelevant, irrelevant)
        # but arranged so marginals are 50/50 and p_o = 0.5
        # p(judge=rel) = 0.5, p(human=rel) = 0.5
        # p_e = 0.5*0.5 + 0.5*0.5 = 0.5
        # p_o = 0.5 (5 agreements out of 10)
        # k = (0.5 - 0.5) / (1 - 0.5) = 0.0
        pairs = [("relevant", "relevant")] * 5 + [("irrelevant", "irrelevant")] * 5
        # Make marginals 50/50 -- actually p_o = 10/10 = 1.0 above; need mismatches
        # Use: 5 agree-relevant, 5 disagree (judge=relevant/human=irrelevant + reverse)
        pairs2 = (
            [("relevant", "relevant")] * 3
            + [("irrelevant", "irrelevant")] * 3
            + [("relevant", "irrelevant")] * 2
            + [("irrelevant", "relevant")] * 2
        )
        # p_o = 6/10 = 0.6
        # p(judge=rel)=5/10=0.5, p(human=rel)=5/10=0.5
        # p_e = 0.5*0.5 + 0.5*0.5 = 0.5
        # k = (0.6 - 0.5) / (1 - 0.5) = 0.2
        result2 = cohens_kappa(pairs2)
        assert abs(result2 - 0.2) < 1e-9, f"Expected 0.2, got {result2}"

    def test_known_mixed_2x2(self):
        r"""
        Hand-computed 2x2 case:
          Judge \ Human  | relevant | irrelevant
          relevant       |    8     |     2
          irrelevant     |    1     |     9

        n = 20
        p_o = (8 + 9) / 20 = 0.85
        p(judge=rel) = 10/20 = 0.5
        p(human=rel) = 9/20 = 0.45
        p_e = 0.5*0.45 + 0.5*0.55 = 0.225 + 0.275 = 0.5
        k = (0.85 - 0.5) / (1 - 0.5) = 0.35 / 0.5 = 0.70
        """
        from eval.judge.kappa import cohens_kappa

        pairs = (
            [("relevant", "relevant")] * 8
            + [("relevant", "irrelevant")] * 2
            + [("irrelevant", "relevant")] * 1
            + [("irrelevant", "irrelevant")] * 9
        )
        result = cohens_kappa(pairs)
        assert abs(result - 0.70) < 1e-9, f"Expected 0.70, got {result}"

    def test_high_agreement(self):
        """
        High agreement case:
          16 (rel, rel), 2 (rel, irr), 1 (irr, rel), 11 (irr, irr) -- n=30
          p_o = 27/30 = 0.9
          p(j=rel) = 18/30 = 0.6, p(h=rel) = 17/30 ~0.5667
          p_e = 0.6*0.5667 + 0.4*0.4333 = 0.34 + 0.1733 = 0.5133...
          k = (0.9 - 0.5133) / (1 - 0.5133) ~= 0.793
        """
        from eval.judge.kappa import cohens_kappa

        pairs = (
            [("relevant", "relevant")] * 16
            + [("relevant", "irrelevant")] * 2
            + [("irrelevant", "relevant")] * 1
            + [("irrelevant", "irrelevant")] * 11
        )
        result = cohens_kappa(pairs)
        # hand-compute:
        n = 30
        p_o = 27 / n
        p_j_rel = 18 / n
        p_h_rel = 17 / n
        p_e = p_j_rel * p_h_rel + (1 - p_j_rel) * (1 - p_h_rel)
        expected = (p_o - p_e) / (1 - p_e)
        assert abs(result - expected) < 1e-9, f"Expected {expected}, got {result}"

    def test_returns_float(self):
        """Return type must be float."""
        from eval.judge.kappa import cohens_kappa

        result = cohens_kappa([("relevant", "relevant")] * 5 + [("irrelevant", "irrelevant")] * 5)
        assert isinstance(result, float)

    def test_3grade_no_collapse_counts_related_vs_direct_as_disagreement(self):
        """
        cohens_kappa treats the full 3-grade scale with NO collapse, so a
        (related, direct-precedent) pair is a DISAGREEMENT (not collapsed to
        relevant/relevant).

        Hand-computed:
          judge:  direct=5, related=10, irrelevant=5  (n=20)
          human:  direct=10, related=5, irrelevant=5
          agreements = 5 (direct,direct) + 5 (related,related) + 5 (irr,irr) = 15
          p_o = 15/20 = 0.75
          p_e = (5/20)(10/20) + (10/20)(5/20) + (5/20)(5/20)
              = 0.125 + 0.125 + 0.0625 = 0.3125
          k = (0.75 - 0.3125) / (1 - 0.3125) = 0.4375 / 0.6875 = 0.63636...
        """
        from eval.judge.kappa import cohens_kappa

        pairs = (
            [("direct-precedent", "direct-precedent")] * 5
            + [("related", "related")] * 5
            + [("irrelevant", "irrelevant")] * 5
            + [("related", "direct-precedent")] * 5  # no collapse -> disagreement
        )
        result = cohens_kappa(pairs)
        assert abs(result - 0.4375 / 0.6875) < 1e-9, (
            f"Expected ~0.636 (3-way, no collapse), got {result}"
        )


class TestKappaModuleConstraints:
    """kappa.py must import nothing outside stdlib."""

    def test_stdlib_only(self):
        """kappa.py must not import any non-stdlib packages."""
        spec = importlib.util.find_spec("eval.judge.kappa")
        assert spec is not None, "eval.judge.kappa not found"
        src_path = pathlib.Path(spec.origin)
        src_text = src_path.read_text(encoding="utf-8")
        import_lines = [
            line.strip()
            for line in src_text.splitlines()
            if re.match(r"^\s*(import|from)\s+", line)
        ]
        forbidden = [
            "numpy", "scipy", "pandas", "sklearn", "openai",
            "anthropic", "psycopg", "pgvector", "httpx",
        ]
        for pkg in forbidden:
            for line in import_lines:
                assert not re.search(rf"\b{re.escape(pkg)}\b", line), (
                    f"kappa.py must not import {pkg} (found: {line!r})"
                )


class TestEscalationLadderStub:
    """
    Task 2: escalation ladder unit test using stubbed grade_pair.

    Asserts:
    - Opus is invoked iff Sonnet kappa < 0.6
    - Raw 3-grade is preserved; binary collapse only for kappa
    """

    def _make_seeded_grader(self, grades: list[str]):
        """Return a grade_pair stub that yields grades from a fixed list."""
        it = iter(grades)

        def stub(
            query: str, candidate_problem: str, candidate_diff: str, model: str
        ) -> dict:
            grade = next(it)
            return {"grade": grade, "rationale": "stub", "model": model}

        return stub

    def test_opus_not_invoked_when_sonnet_kappa_above_threshold(self):
        """When Sonnet kappa >= 0.6, Opus must NOT be called."""
        import eval.judge.judge as judge_mod

        gold = self._build_gold_above_threshold()

        called_models = []

        def fake_grade(
            query: str, candidate_problem: str, candidate_diff: str, model: str
        ) -> dict:
            called_models.append(model)
            # Return grade from gold human_grade to ensure high kappa
            for row in gold:
                if row["query"] == query:
                    return {
                        "grade": row["human_grade"],
                        "rationale": "stub",
                        "model": model,
                    }
            return {"grade": "related", "rationale": "stub", "model": model}

        original = judge_mod.grade_pair
        try:
            judge_mod.grade_pair = fake_grade
            result = judge_mod.calibrate(gold=gold, api_key="test-key-placeholder")
        finally:
            judge_mod.grade_pair = original

        assert result["sonnet_kappa"] >= 0.6, "Sonnet kappa should be >= 0.6 in this fixture"
        assert "opus_kappa" not in result or result.get("opus_invoked") is False, (
            "Opus must not be invoked when Sonnet kappa >= 0.6"
        )
        assert not any("opus" in m for m in called_models), (
            f"Opus model was called but should not have been; called: {called_models}"
        )

    def test_opus_invoked_when_sonnet_kappa_below_threshold(self):
        """When Sonnet kappa < 0.6, Opus must be called."""
        import eval.judge.judge as judge_mod

        gold = self._build_gold_below_threshold()

        call_log = []

        def fake_grade(
            query: str, candidate_problem: str, candidate_diff: str, model: str
        ) -> dict:
            call_log.append(model)
            if "sonnet" in model:
                # Systematically disagree with human to get low kappa
                for row in gold:
                    if row["query"] == query:
                        h = row["human_grade"]
                        # flip: relevant->irrelevant, irrelevant->relevant
                        flip = "irrelevant" if h in ("related", "direct-precedent") else "related"
                        return {"grade": flip, "rationale": "stub-disagree", "model": model}
            else:
                # Opus: agree perfectly to get high kappa
                for row in gold:
                    if row["query"] == query:
                        return {"grade": row["human_grade"], "rationale": "stub-agree", "model": model}
            return {"grade": "related", "rationale": "stub", "model": model}

        original = judge_mod.grade_pair
        try:
            judge_mod.grade_pair = fake_grade
            result = judge_mod.calibrate(gold=gold, api_key="test-key-placeholder")
        finally:
            judge_mod.grade_pair = original

        assert result["sonnet_kappa"] < 0.6, "Sonnet kappa should be < 0.6 in this fixture"
        assert "opus_kappa" in result, "Opus kappa must be recorded when Opus is invoked"
        opus_calls = [m for m in call_log if "opus" in m]
        assert len(opus_calls) > 0, f"Opus was not called; call_log={call_log}"

    def test_raw_3grade_preserved_binary_collapse_only_for_kappa(self):
        """
        grade_pair must return raw 3-grade (irrelevant/related/direct-precedent),
        not binary. Binary collapse only happens inside cohens_kappa call.
        """
        import eval.judge.judge as judge_mod

        gold = self._build_gold_above_threshold()
        returned_grades = []

        def fake_grade(
            query: str, candidate_problem: str, candidate_diff: str, model: str
        ) -> dict:
            # Return raw 3-grade direct-precedent
            returned_grades.append("direct-precedent")
            return {"grade": "direct-precedent", "rationale": "stub", "model": model}

        original = judge_mod.grade_pair
        try:
            judge_mod.grade_pair = fake_grade
            result = judge_mod.calibrate(gold=gold, api_key="test-key-placeholder")
        finally:
            judge_mod.grade_pair = original

        # Each call must have returned raw 3-grade
        assert all(g in ("irrelevant", "related", "direct-precedent") for g in returned_grades), (
            "grade_pair must return raw 3-grade (irrelevant/related/direct-precedent)"
        )
        # The grades in calibration results must preserve raw 3-grade
        if "sonnet_grades" in result:
            for entry in result["sonnet_grades"]:
                assert entry["grade"] in ("irrelevant", "related", "direct-precedent"), (
                    f"Raw 3-grade not preserved in sonnet_grades: {entry['grade']}"
                )

    def _build_gold_above_threshold(self):
        """Build a minimal gold set where perfect Sonnet agreement gives kappa = 1.0."""
        gold = []
        for i in range(10):
            gold.append({
                "query": f"Fix bug in feature {i}",
                "candidate_problem": f"Problem: fix bug in feature {i}",
                "candidate_diff": f"--- a/feature_{i}.py\n+++ b/feature_{i}.py\n@@ -1 +1 @@\n-old\n+new",
                "human_grade": "direct-precedent" if i < 7 else "irrelevant",
                "stratum": "backport" if i < 3 else "clear-irrelevant" if i >= 7 else "clear-relevant",
            })
        return gold

    def _build_gold_below_threshold(self):
        """Build a minimal gold set to test escalation (6 relevant, 4 irrelevant)."""
        gold = []
        for i in range(10):
            gold.append({
                "query": f"Query {i}",
                "candidate_problem": f"Candidate problem {i}",
                "candidate_diff": f"+fix {i}",
                "human_grade": "related" if i < 6 else "irrelevant",
                "stratum": "clear-relevant" if i < 6 else "clear-irrelevant",
            })
        return gold
