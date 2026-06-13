"""
senrah.ingester.diff_files — derive changed-file paths from a unified diff.

Design B's diff-less traversal yields RawPR.files_changed == [] (the file list
would need a paginated get_files() call per PR). The Ingester already fetches
the full diff for every survivor, and the diff names every touched file in its
`diff --git a/<old> b/<new>` headers — including binary files, which have no
---/+++ hunk lines. Parsing those headers populates pull_requests.files_changed
at ZERO extra API cost.

The b-side (new path) is recorded: for renames that is the path that exists
after the merge, which is what file-overlap consumers (search output, held-out
ground truth) want.
"""

from __future__ import annotations

import re

# `diff --git a/<old> b/<new>`; git quotes paths containing spaces/specials.
# Non-greedy a-side + anchored b-side keeps the common and rename cases right;
# a path that itself contains ` b/` is ambiguous in this format for any parser.
_DIFF_GIT_RE = re.compile(r'^diff --git "?a/(.*?)"? "?b/(.*?)"?$')


def parse_diff_files(diff: str) -> list[str]:
    """Return the b-side paths named by `diff --git` headers, deduped, in order.

    Args:
        diff: Unified diff text (GitHub `.diff` media type).

    Returns:
        Changed-file paths (new path for renames), in first-seen order.
        Empty list for empty/None-ish input.
    """
    if not diff:
        return []
    seen: set[str] = set()
    files: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        match = _DIFF_GIT_RE.match(line)
        if match is None:
            continue
        path = match.group(2)
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    return files
