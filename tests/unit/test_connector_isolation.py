"""
tests/unit/test_connector_isolation.py -- Import-graph guard for the connector seam.

Modeled on tests/unit/test_judge_isolation.py. Scans all Python source files
under src/senrah/connectors/ and asserts that connectors import nothing from
senrah outside the connectors package itself.

This guard exists because the connector interface is the core extensibility
seam: a new source (GitLab, Bitbucket, internal Git hosting) must require no
changes to the Indexer or the MCP server. That only holds if connectors stay
ignorant of the indexer, ingester, DB, MCP, and CLI layers -- the ingester
depends on the ConnectorProtocol, never the other way around.
"""

from __future__ import annotations

import pathlib
import re


REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
CONNECTORS_ROOT = REPO_ROOT / "src" / "senrah" / "connectors"

FORBIDDEN_PACKAGES = [
    "senrah.indexer",
    "senrah.ingester",
    "senrah.db",
    "senrah.mcp",
    "senrah.cli",
]


class TestConnectorImportBoundary:
    """src/senrah/connectors must not import the layers that depend on it."""

    def _get_connector_sources(self) -> list[pathlib.Path]:
        """Return all .py files under src/senrah/connectors/."""
        assert CONNECTORS_ROOT.exists(), (
            f"src/senrah/connectors not found at {CONNECTORS_ROOT}"
        )
        return list(CONNECTORS_ROOT.rglob("*.py"))

    def _extract_import_lines(self, src_text: str) -> list[str]:
        """Extract all import statement lines from source text."""
        return [
            line.strip()
            for line in src_text.splitlines()
            if re.match(r"^\s*(import|from)\s+", line)
        ]

    def test_no_forbidden_layer_imports(self):
        """Connectors must not import indexer, ingester, db, mcp, or cli."""
        sources = self._get_connector_sources()
        assert sources, "No Python files found under src/senrah/connectors/"
        violations = []
        for path in sources:
            src_text = path.read_text(encoding="utf-8")
            import_lines = self._extract_import_lines(src_text)
            for pkg in FORBIDDEN_PACKAGES:
                for line in import_lines:
                    if re.search(rf"\b{re.escape(pkg)}\b", line):
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)}: imports {pkg!r}: {line!r}"
                        )
        assert not violations, (
            "Connector import boundary violated (connectors must only see"
            " senrah.connectors.*):\n" + "\n".join(violations)
        )

    def test_only_connector_package_senrah_imports(self):
        """Any `senrah.*` import inside connectors must be senrah.connectors.*."""
        sources = self._get_connector_sources()
        violations = []
        for path in sources:
            src_text = path.read_text(encoding="utf-8")
            import_lines = self._extract_import_lines(src_text)
            for line in import_lines:
                match = re.search(r"\bsenrah\.(\w+)", line)
                if match and match.group(1) != "connectors":
                    violations.append(f"{path.relative_to(REPO_ROOT)}: {line!r}")
        assert not violations, (
            "Connectors import senrah modules outside senrah.connectors:\n"
            + "\n".join(violations)
        )

    def test_guard_detects_violation(self):
        """Self-test: the guard logic detects a forbidden import if one appears."""
        fake_source = "from senrah.ingester import Ingester\nimport senrah.db.repo\n"
        import_lines = self._extract_import_lines(fake_source)
        found = any(
            re.search(rf"\b{re.escape(pkg)}\b", line)
            for pkg in FORBIDDEN_PACKAGES
            for line in import_lines
        )
        assert found, "Boundary guard logic failed to detect a seeded violation"
