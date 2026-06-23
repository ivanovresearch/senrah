"""
tests/unit/test_judge_isolation.py -- Import-graph guard: src/senrah must not import anthropic.

Modeled on tests/unit/test_scoring.py::TestScoringModuleConstraints.
Scans all Python source files under src/senrah/ and asserts that no module
imports `anthropic` or any other LLM client.

This guard exists because anthropic is an optional eval-only dependency
(pyproject.toml [project.optional-dependencies] eval). Including it in
src/senrah would break 'pip install senrah' for users who don't want the
eval harness. (D-17, RESEARCH pitfall 5)
"""

from __future__ import annotations

import pathlib
import re


REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
SRC_ROOT = REPO_ROOT / "src" / "senrah"


class TestSenrahImportGraph:
    """src/senrah must not import LLM clients or eval-only packages."""

    def _get_senrah_sources(self) -> list[pathlib.Path]:
        """Return all .py files under src/senrah/."""
        assert SRC_ROOT.exists(), f"src/senrah not found at {SRC_ROOT}"
        return list(SRC_ROOT.rglob("*.py"))

    def _extract_import_lines(self, src_text: str) -> list[str]:
        """Extract all import statement lines from source text."""
        return [
            line.strip()
            for line in src_text.splitlines()
            if re.match(r"^\s*(import|from)\s+", line)
        ]

    def test_no_anthropic_import(self):
        """src/senrah must not import anthropic (eval-only dep, D-17)."""
        sources = self._get_senrah_sources()
        assert sources, "No Python files found under src/senrah/"
        violations = []
        for path in sources:
            src_text = path.read_text(encoding="utf-8")
            import_lines = self._extract_import_lines(src_text)
            for line in import_lines:
                if re.search(r"\banthopic\b|\banthopic\b", line):
                    violations.append(f"{path.relative_to(REPO_ROOT)}: {line!r}")
                if re.search(r"\banthopic\b", line):
                    violations.append(f"{path.relative_to(REPO_ROOT)}: {line!r}")
        assert not violations, (
            "src/senrah imports anthropic (must stay LLM-free, D-17):\n"
            + "\n".join(violations)
        )

    def test_no_anthropic_import_strict(self):
        """Strict check: src/senrah/* source lines must not reference 'anthropic'."""
        sources = self._get_senrah_sources()
        violations = []
        for path in sources:
            src_text = path.read_text(encoding="utf-8")
            import_lines = self._extract_import_lines(src_text)
            for line in import_lines:
                if re.search(r"\banthroptic\b", line):
                    violations.append(f"{path.relative_to(REPO_ROOT)}: {line!r}")
                # Correct spelling check
                if "anthropic" in line:
                    violations.append(f"{path.relative_to(REPO_ROOT)}: {line!r}")
        assert not violations, (
            "src/senrah imports anthropic (must stay LLM-free, D-17):\n"
            + "\n".join(violations)
        )

    def test_no_llm_client_imports(self):
        """
        src/senrah must not import any LLM client packages.
        This covers anthropic, openai (already present is OK for embeddings
        in indexer), but anthropic specifically must never appear.
        """
        sources = self._get_senrah_sources()
        llm_clients = ["anthropic"]  # openai is allowed (embeddings); anthropic is not
        violations = []
        for path in sources:
            src_text = path.read_text(encoding="utf-8")
            import_lines = self._extract_import_lines(src_text)
            for pkg in llm_clients:
                for line in import_lines:
                    if re.search(rf"\b{re.escape(pkg)}\b", line):
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)}: imports {pkg!r}: {line!r}"
                        )
        assert not violations, (
            "src/senrah imports LLM clients that must stay in eval/ (D-17):\n"
            + "\n".join(violations)
        )

    def test_isolation_test_fails_if_anthropic_imported(self):
        """
        Self-test: if we add 'import anthropic' to a temp scan buffer,
        the isolation check correctly detects it.
        """
        fake_source = "import anthropic\nfrom anthropic import Anthropic\n"
        import_lines = self._extract_import_lines(fake_source)
        found = any(re.search(r"\banthroptic\b", line) for line in import_lines)
        found_correct = any("anthropic" in line for line in import_lines)
        assert found_correct, (
            "Isolation guard logic must detect 'import anthropic' -- self-test failure"
        )
