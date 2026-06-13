"""
tests/unit/test_scoring.py — Unit tests for senrah.scoring.composite_score.

Covers SEARCH-01: score = problem_weight × problem_sim + solution_weight × solution_sim.
The function is pure (no I/O, no deps) so all tests run without Docker or OpenAI.

Decision D-09: composite score formula with configurable weights default 0.6/0.4.
"""

from __future__ import annotations

import importlib
import sys

import pytest

from senrah.scoring import composite_score


class TestCompositeScoreFormula:
    """Basic formula correctness tests (D-09)."""

    def test_canonical_example(self):
        """D-11 example: p=0.72, s=0.31 → 0.6*0.72 + 0.4*0.31 = 0.432 + 0.124 = 0.556."""
        result = composite_score(problem_sim=0.72, solution_sim=0.31)
        assert abs(result - 0.556) < 1e-9, f"Expected 0.556, got {result}"

    def test_default_weights(self):
        """Default weights are 0.6/0.4 per D-09."""
        result = composite_score(problem_sim=1.0, solution_sim=1.0)
        assert abs(result - 1.0) < 1e-9, "With both sims=1.0 and weights summing to 1.0, score must be 1.0"

    def test_zero_sims(self):
        """Both similarities at zero → score is zero."""
        result = composite_score(problem_sim=0.0, solution_sim=0.0)
        assert result == 0.0

    def test_problem_weight_dominant(self):
        """With problem_sim=1.0, solution_sim=0.0, score equals problem_weight."""
        result = composite_score(problem_sim=1.0, solution_sim=0.0)
        assert abs(result - 0.6) < 1e-9

    def test_solution_weight_dominant(self):
        """With problem_sim=0.0, solution_sim=1.0, score equals solution_weight."""
        result = composite_score(problem_sim=0.0, solution_sim=1.0)
        assert abs(result - 0.4) < 1e-9

    def test_explicit_weights_override(self):
        """Explicit weights override defaults (configurable per D-09)."""
        result = composite_score(problem_sim=0.8, solution_sim=0.6, problem_weight=0.5, solution_weight=0.5)
        expected = 0.5 * 0.8 + 0.5 * 0.6  # = 0.7
        assert abs(result - expected) < 1e-9

    def test_non_standard_weights(self):
        """Weights don't have to sum to 1 (formula is a weighted sum, not average)."""
        result = composite_score(problem_sim=0.5, solution_sim=0.5, problem_weight=0.7, solution_weight=0.3)
        expected = 0.7 * 0.5 + 0.3 * 0.5  # = 0.5
        assert abs(result - expected) < 1e-9

    def test_equal_sims_and_weights(self):
        """With equal sims and 0.6/0.4 weights, score == sim value."""
        result = composite_score(problem_sim=0.5, solution_sim=0.5)
        # 0.6*0.5 + 0.4*0.5 = 0.5
        assert abs(result - 0.5) < 1e-9

    def test_high_score_example(self):
        """Typical high-match scenario."""
        result = composite_score(problem_sim=0.90, solution_sim=0.80)
        expected = 0.6 * 0.90 + 0.4 * 0.80  # = 0.54 + 0.32 = 0.86
        assert abs(result - expected) < 1e-9

    def test_returns_float(self):
        """Return type is float."""
        result = composite_score(problem_sim=0.5, solution_sim=0.5)
        assert isinstance(result, float)


class TestScoringModuleConstraints:
    """Structural constraints: pure function, no heavy deps (STATE.md)."""

    def test_no_db_imports(self):
        """scoring.py must not import from senrah.db or senrah.cli."""
        import senrah.scoring as m
        source = importlib.util.find_spec("senrah.scoring")
        assert source is not None
        # Re-read the source to check imports
        import pathlib
        src_path = pathlib.Path(source.origin)
        src_text = src_path.read_text()
        assert "senrah.db" not in src_text, "scoring.py must not import senrah.db"
        assert "senrah.cli" not in src_text, "scoring.py must not import senrah.cli"
        assert "senrah.indexer" not in src_text, "scoring.py must not import senrah.indexer"

    def test_no_external_deps(self):
        """scoring.py should have no non-stdlib import statements (pure function requirement)."""
        import pathlib
        import importlib.util
        import re
        source = importlib.util.find_spec("senrah.scoring")
        src_path = pathlib.Path(source.origin)
        src_text = src_path.read_text()
        # Only stdlib imports allowed: typing, __future__ etc.
        # Must not import openai, tiktoken, psycopg, etc.
        # Check for actual import statements (not occurrences in comments/docstrings)
        import_lines = [
            line.strip()
            for line in src_text.splitlines()
            if re.match(r"^\s*(import|from)\s+", line)
        ]
        forbidden = ["openai", "tiktoken", "psycopg", "pgvector", "yaml", "requests", "httpx"]
        for pkg in forbidden:
            for line in import_lines:
                assert not re.search(rf"\b{re.escape(pkg)}\b", line), (
                    f"scoring.py must not import {pkg} (found: {line!r})"
                )
