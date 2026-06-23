"""
eval/knownitem/generate_v3_manifest.py -- One-shot script to generate manifest-v3.json.

Run this script once to freeze the v3 known-item manifest:

    python eval/knownitem/generate_v3_manifest.py

No GitHub API call is made; the v3 path reuses v2 query text and only
recomputes relevant_prs from the fuzzy cluster map.

No database connection is required.
"""

from __future__ import annotations

import sys
import pathlib

# Ensure the repo root is on the path so eval.* imports work.
REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.knownitem.build_manifest import build_v3

if __name__ == "__main__":
    print("Building manifest-v3.json from cluster map...")
    manifest = build_v3()
    print(
        f"Done: {len(manifest['queries'])} queries, "
        f"{len(manifest['corrections'])} corrections, "
        f"fingerprint={manifest['corpus_fingerprint']['hash'][:16]}..."
    )
    print("Written to eval/knownitem/manifest-v3.json")
