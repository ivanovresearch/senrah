"""
eval/knownitem/build_manifest.py — freeze the known-item evaluation manifest.

Known-item retrieval eval (designed 2026-06-11, ratified after the A/B run):
for every corpus PR with a linked issue, the QUERY is the issue's title+body
(the "symptom language" — never embedded anywhere in the index; the problem
embedding is built from the PR's own title+body) and the single relevant item
is that PR. Label precision is 1.0 by construction; no auxiliary label source.

Anti-leak rules applied here:
- all `#NNNNN` tokens are stripped from the query (PR bodies embed "Fixes #N",
  so issue numbers are real, if weak, leak tokens);
- issues that turn out to be pull requests or are inaccessible are skipped
  and recorded in the manifest header.

Backport rule (v2): a fix and its release-branch backport are the SAME
solution under different PR numbers. relevant(X) = {X} ∪ {corpus PRs whose
normalized title — `[release/…]`/`[main]` prefixes stripped, whitespace
collapsed, casefolded — equals X's normalized title}. Without this rule a
retrieval that returns the backport of the target is scored as a miss
(observed in the v1 baseline: target 37674 "missed" to its own backport
38066).

Output: eval/knownitem/manifest.json — frozen; the eval runner consumes it.
Re-running this script after corpus changes produces a NEW manifest version
(corpus fingerprint is recorded), old results stay comparable only within
their manifest version.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time

import httpx
import psycopg
from dotenv import load_dotenv

DSN = "postgresql://harness:harness@localhost:5432/harness"
OUT = pathlib.Path(__file__).parent / "manifest.json"

# `[release/10.0]`, `[main]`, `[release/9.0-staging]`… — possibly stacked.
_BRANCH_PREFIX = re.compile(r"^\s*\[(release/[^\]]+|main)\]\s*", re.IGNORECASE)


def _normalize_title(title: str) -> str:
    """Strip branch prefixes, collapse whitespace, casefold."""
    t, n = _BRANCH_PREFIX.subn("", title)
    while n:
        t, n = _BRANCH_PREFIX.subn("", t)
    return re.sub(r"\s+", " ", t).strip().casefold()


def main() -> None:
    load_dotenv(".env")
    token = os.environ["GITHUB_TOKEN"]
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }

    conn = psycopg.connect(DSN)
    rows = conn.execute(
        "SELECT number, linked_issue, merged_at FROM pull_requests "
        "WHERE linked_issue IS NOT NULL ORDER BY merged_at"
    ).fetchall()
    all_titles = conn.execute("SELECT number, title FROM pull_requests").fetchall()
    corpus = conn.execute(
        "SELECT count(*), min(merged_at)::date::text, max(merged_at)::date::text "
        "FROM pull_requests"
    ).fetchone()
    conn.close()

    # Backport groups: normalized title -> set of PR numbers.
    title_groups: dict[str, set[int]] = {}
    for number, title in all_titles:
        title_groups.setdefault(_normalize_title(title), set()).add(number)

    queries = []
    skipped = []
    with httpx.Client(headers=headers, timeout=30) as client:
        for pr_number, linked, merged_at in rows:
            issue_num = int(linked.lstrip("#"))
            r = client.get(
                f"https://api.github.com/repos/dotnet/efcore/issues/{issue_num}"
            )
            if r.status_code != 200:
                skipped.append({"pr": pr_number, "issue": issue_num, "reason": f"http {r.status_code}"})
                continue
            issue = r.json()
            if "pull_request" in issue:
                skipped.append({"pr": pr_number, "issue": issue_num, "reason": "linked ref is a PR"})
                continue
            title = issue.get("title") or ""
            body = issue.get("body") or ""
            query = re.sub(r"#\d+", "", f"{title}\n\n{body}").strip()
            if len(query) < 30:
                skipped.append({"pr": pr_number, "issue": issue_num, "reason": "issue text too short"})
                continue
            target_title = next(t for n, t in all_titles if n == pr_number)
            relevant = sorted(title_groups[_normalize_title(target_title)])
            queries.append(
                {
                    "target_pr": pr_number,
                    "relevant_prs": relevant,  # target + its backports/original
                    "issue": issue_num,
                    "merged_at": merged_at.isoformat(),
                    "query": query,
                }
            )
            time.sleep(0.3)

    manifest = {
        "version": "v2-knownitem-backports",
        "corpus": {"prs": corpus[0], "min_merged": corpus[1], "max_merged": corpus[2]},
        "rules": "query = linked issue title+body, #NNNNN stripped; "
        "relevant = {target_pr} + same-normalized-title corpus PRs (backport rule); "
        "ranking metrics only (threshold decoupled per protocol)",
        "skipped": skipped,
        "queries": queries,
    }
    OUT.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"queries: {len(queries)}, skipped: {len(skipped)}, corpus: {corpus}")


if __name__ == "__main__":
    main()
