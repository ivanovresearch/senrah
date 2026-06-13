"""
eval/ab/score.py — objective outcome metrics for one A/B run.

Usage: python eval/ab/score.py <pr_number> <arm>

Compares the agent's edited tree (C:/Work/efcore-ab/runs/pr-<pr>-<arm>)
against the pristine snapshot (C:/Work/efcore-ab/snapshots/pr-<pr>) and the
REAL merged fix (diff + files_changed from the senrah DB), and prints:

- (a) file_recall: |real fix files ∩ agent-touched files| / |real fix files|
- (b) symbol_overlap: recall of "symbols" (C# type/member names seen in
  changed lines + hunk-header context) of the real diff in the agent diff.
- the raw file lists, for the report.

Also writes the agent's unified diff to eval/ab/runs/pr-<pr>-<arm>.patch
(via `git diff --no-index`), so the blind judge reads patches, not trees.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

AB_ROOT = pathlib.Path("C:/Work/efcore-ab")
OUT_DIR = pathlib.Path(__file__).parent / "runs"

_DSN = "postgresql://harness:harness@localhost:5432/harness"

# C# declarations and hunk-header context: class/interface/struct names,
# method names before '(', property names. Deliberately coarse — both sides
# are extracted by the SAME rule, so the comparison is fair.
_SYMBOL_RE = re.compile(
    r"\b(?:class|interface|struct|record|enum)\s+(\w+)"
    r"|\b(\w+)\s*\("
)
_STOPWORDS = {
    "if", "while", "for", "foreach", "switch", "catch", "using", "lock",
    "return", "new", "nameof", "typeof", "sizeof", "throw", "base", "this",
    "Append", "ToString", "Equals", "GetHashCode", "Add", "Get", "Set",
}


def _symbols(diff_text: str) -> set[str]:
    syms: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("@@", "+", "-")):
            for m in _SYMBOL_RE.finditer(line):
                name = m.group(1) or m.group(2)
                if name and name not in _STOPWORDS and len(name) > 2:
                    syms.add(name)
    return syms


def main() -> None:
    pr, arm = int(sys.argv[1]), sys.argv[2]
    snap = AB_ROOT / "snapshots" / f"pr-{pr}"
    run = AB_ROOT / "runs" / f"pr-{pr}-{arm}"

    import psycopg

    conn = psycopg.connect(_DSN)
    row = conn.execute(
        "SELECT files_changed, diff FROM pull_requests WHERE number = %s", (pr,)
    ).fetchone()
    conn.close()
    real_files_raw, real_diff = row
    real_files = (
        real_files_raw
        if isinstance(real_files_raw, list)
        else json.loads(real_files_raw)
    )

    # Agent diff via git (exit code 1 just means "differences found").
    proc = subprocess.run(
        ["git", "diff", "--no-index", "--src-prefix=a/", "--dst-prefix=b/",
         str(snap), str(run)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    agent_diff = proc.stdout

    # Touched files: b-side paths relative to the run dir; drop SOLUTION.md.
    marker = f"pr-{pr}-{arm}/"
    touched: list[str] = []
    for line in agent_diff.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip().strip('"').replace("\\", "/")
            p = re.sub(r"/{2,}", "/", p)
            if p == "/dev/null":
                continue
            idx = p.find(marker)
            if idx != -1:
                p = p[idx + len(marker):]
            if p != "SOLUTION.md":
                touched.append(p)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"pr-{pr}-{arm}.patch").write_text(agent_diff, encoding="utf-8")

    inter = sorted(set(real_files) & set(touched))
    file_recall = len(inter) / len(real_files) if real_files else 0.0

    real_syms = _symbols(real_diff)
    agent_syms = _symbols(agent_diff)
    sym_inter = real_syms & agent_syms
    sym_recall = len(sym_inter) / len(real_syms) if real_syms else 0.0

    result = {
        "pr": pr, "arm": arm,
        "file_recall": round(file_recall, 3),
        "symbol_recall": round(sym_recall, 3),
        "real_files": real_files,
        "touched_files": touched,
        "files_hit": inter,
        "real_symbols": len(real_syms),
        "symbols_hit": len(sym_inter),
    }
    (OUT_DIR / f"pr-{pr}-{arm}.score.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
