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
    corpus = conn.execute(
        "SELECT count(*), min(merged_at)::date::text, max(merged_at)::date::text "
        "FROM pull_requests"
    ).fetchone()
    conn.close()

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
            queries.append(
                {
                    "target_pr": pr_number,
                    "issue": issue_num,
                    "merged_at": merged_at.isoformat(),
                    "query": query,
                }
            )
            time.sleep(0.3)

    manifest = {
        "version": "v1-knownitem",
        "corpus": {"prs": corpus[0], "min_merged": corpus[1], "max_merged": corpus[2]},
        "rules": "query = linked issue title+body, #NNNNN stripped; relevant = {target_pr}; "
        "ranking metrics only (threshold decoupled per protocol)",
        "skipped": skipped,
        "queries": queries,
    }
    OUT.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"queries: {len(queries)}, skipped: {len(skipped)}, corpus: {corpus}")


if __name__ == "__main__":
    main()
