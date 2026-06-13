"""Senrah — semantic PR search for AI coding agents.

The package version has a single source of truth: ``[project] version`` in
``pyproject.toml``. We expose it here by reading the *installed* distribution
metadata rather than duplicating the literal string. When running from an
uninstalled source tree (no metadata), fall back to a sentinel.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # `version()` can return None if a dist-info exists but its METADATA lacks a
    # Version field (e.g. a corrupt editable install) — treat that like missing.
    __version__ = version("senrah") or "0.0.0+unknown"
except PackageNotFoundError:  # running from source without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
