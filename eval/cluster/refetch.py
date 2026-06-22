"""
eval/cluster/refetch.py — Cached commit-message GitHub fallback for backport detection.

This module is the ONLY component in the cluster detector that touches the network.
It is only invoked for the ambiguous tail: diff-similar pairs with no other corroboration.

Caching strategy (D-01):
  Responses are cached to eval/cluster/.commitcache/<pr>.json so re-runs and the
  Phase 10/11 deep re-run never re-fetch. In steady state, zero network calls.

Auth pattern (from eval/knownitem/build_manifest.py lines 58-64, 84-89):
  token = os.environ["GITHUB_TOKEN"]
  headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

Cherry-pick detection:
  Scans commit messages for "cherry picked from commit <SHA>".
  A matching source SHA across two PRs is a near-certain corroboration edge.

Security (T-09-01):
  GITHUB_TOKEN read from os.environ ONLY; never written into .commitcache or logs.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time
from typing import Any

import httpx

# Cache directory relative to this file.
_CACHE_DIR = pathlib.Path(__file__).parent / ".commitcache"

# Cherry-pick provenance pattern in commit messages.
_CHERRY_PICK_RE = re.compile(r"cherry picked from commit ([0-9a-f]{40})", re.IGNORECASE)

# GitHub repo for dotnet/efcore (the target corpus).
_REPO = "dotnet/efcore"


def _cache_path(pr_number: int) -> pathlib.Path:
    """Return the cache file path for a given PR number."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{pr_number}.json"


def fetch_commits(
    pr_number: int,
    *,
    repo: str = _REPO,
    token: str | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Fetch commit list for a PR from GitHub (read-through cache).

    Returns a list of commit dicts from the GitHub API. Caches the response
    to disk; on subsequent calls returns the cached data without a network request.

    Args:
        pr_number: GitHub PR number.
        repo: GitHub repo path (e.g. "dotnet/efcore").
        token: GitHub token. If None, reads from os.environ["GITHUB_TOKEN"].
        force_refresh: If True, bypass cache and re-fetch from GitHub.

    Returns:
        List of commit dicts. Empty list on HTTP error.
    """
    cache_file = _cache_path(pr_number)

    # Read-through: return cached data if available.
    if not force_refresh and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    # Resolve token from env if not provided.
    if token is None:
        token = os.environ["GITHUB_TOKEN"]

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }

    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/commits"
    with httpx.Client(headers=headers, timeout=30) as client:
        r = client.get(url)
        time.sleep(0.3)  # politeness delay (GitHub secondary rate limit)

    if r.status_code != 200:
        # Record the failure as an empty list so we don't re-fetch on every run.
        result: list[dict[str, Any]] = []
    else:
        result = r.json()

    # Cache to disk (never contains the token — only public commit metadata).
    cache_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def extract_cherry_pick_shas(commits: list[dict[str, Any]]) -> set[str]:
    """Extract cherry-pick source SHAs from commit messages.

    Returns a set of 40-char SHAs found in "cherry picked from commit <SHA>" patterns.
    """
    shas: set[str] = set()
    for commit in commits:
        message = commit.get("commit", {}).get("message", "")
        for m in _CHERRY_PICK_RE.finditer(message):
            shas.add(m.group(1))
    return shas


def find_cherry_pick_corroboration(
    pr_a: int,
    pr_b: int,
    *,
    repo: str = _REPO,
    token: str | None = None,
) -> bool:
    """Return True if pr_a and pr_b share a cherry-pick SHA (near-certain backport edge).

    Fetches commits for both PRs (read-through cache) and checks whether any
    cherry-pick source SHA from one PR matches the other's cherry-pick SHAs.
    Only called for the ambiguous diff-similar-but-uncorroborated tail.

    Security: token is read from os.environ["GITHUB_TOKEN"] if not provided;
    never written into any output artifact.
    """
    commits_a = fetch_commits(pr_a, repo=repo, token=token)
    commits_b = fetch_commits(pr_b, repo=repo, token=token)
    shas_a = extract_cherry_pick_shas(commits_a)
    shas_b = extract_cherry_pick_shas(commits_b)

    # A shared cherry-pick SHA means one PR cherry-picked a commit from the other.
    if shas_a & shas_b:
        return True

    # Also check if the merge commits themselves share a cherry-pick source.
    # (one may have cherry-picked from the other's merge commit)
    all_shas_a = {c.get("sha", "") for c in commits_a}
    all_shas_b = {c.get("sha", "") for c in commits_b}
    if shas_a & all_shas_b or shas_b & all_shas_a:
        return True

    return False
