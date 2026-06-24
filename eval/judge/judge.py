"""
eval/judge/judge.py -- Blind calibration harness with Sonnet->Opus escalation ladder.

Runs a 3-grade LLM judge (irrelevant/related/direct-precedent) against the gold
set (gold.jsonl) and computes Cohen's kappa to calibrate judge reliability.

Escalation ladder (D-15):
  1. Grade the gold set with Sonnet 4.6
  2. Compute kappa (multi-category, no collapse) via kappa.py
  3. If kappa < 0.6, escalate to Opus 4.8 and re-score
  4. If even Opus kappa < 0.6, record advisory-only verdict

Raw 3-grade output (irrelevant/related/direct-precedent) is PRESERVED in all
result records and is also what kappa is computed over -- the original
binary-collapse (D-16) was dropped because the judge never emits "irrelevant",
which made the collapsed kappa degenerate (always 0).

Usage (one-time live calibration):
  python eval/judge/judge.py

Calls Claude (Sonnet 4.6 / Opus 4.8) via an OpenAI-compatible endpoint
(OpenRouter by default). Requires OPENAI_API_KEY in environment (or .env);
override the endpoint with OPENAI_BASE_URL. The live calibration result is a
recorded artifact, not a CI assertion.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

from dotenv import load_dotenv

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

HERE = pathlib.Path(__file__).parent
GOLD_PATH = HERE / "gold.jsonl"
RESULTS_PATH = HERE / "calibration_results.json"

# The judge calls Claude through an OpenAI-compatible endpoint (OpenRouter),
# the same transport the indexer uses for embeddings (senrah.yaml embed.base_url).
# This keeps the eval LLM-free at the package level: only the already-present
# `openai` SDK is used -- no `anthropic` dependency is required (D-17).
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
SONNET_MODEL = "anthropic/claude-sonnet-4.6"
OPUS_MODEL = "anthropic/claude-opus-4.8"
KAPPA_FLOOR = 0.6

JUDGE_PROMPT_TEMPLATE = """\
You are evaluating whether a historical code-change precedent is relevant to a current task.

CURRENT TASK:
{query}

CANDIDATE PRECEDENT:
Problem: {candidate_problem}

Diff excerpt:
{candidate_diff}

Evaluate the relevance of this precedent to the current task on a 3-grade scale:
- irrelevant: The precedent addresses a completely different problem or domain.
- related: The precedent is in the same area or uses similar patterns, but does not
  directly apply to this task.
- direct-precedent: The precedent solves the same or nearly identical problem and the
  approach directly transfers to the current task.

Provide a one-line rationale explaining your judgment, then on the final line output
EXACTLY the following format (nothing else on that line):
GRADE: <irrelevant|related|direct-precedent>
"""


def _provenance() -> dict:
    """Record how the judge was served, so the calibration artifact is honest.

    kappa is computed over models reached through an OpenAI-compatible endpoint
    (OpenRouter by default), NOT the direct Anthropic API. The slugs are
    OpenRouter routing identifiers; the exact upstream model build is not
    independently pinned here. JUDGE-02 must read kappa with this transport in
    mind.
    """
    return {
        "transport": "openai-compatible",
        "endpoint": os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL),
        "models": {"sonnet": SONNET_MODEL, "opus": OPUS_MODEL},
        "note": (
            "Models served via the OpenAI-compatible endpoint above (OpenRouter "
            "by default). Slugs are OpenRouter routing identifiers, not direct "
            "Anthropic API model IDs; upstream model build is not independently "
            "pinned. Blind calibration: run before any depth measurement."
        ),
    }


def grade_pair(query: str, candidate_problem: str, candidate_diff: str, model: str) -> dict:
    """
    Call the Anthropic API to grade a (query, candidate) pair.

    Args:
        query: The task/problem description to be solved.
        candidate_problem: The problem description of the candidate precedent.
        candidate_diff: The diff excerpt of the candidate precedent.
        model: Anthropic model name (e.g. "claude-sonnet-4-6").

    Returns:
        dict with keys: grade (raw 3-grade), rationale (str), model (str).
    """
    from openai import OpenAI  # noqa: PLC0415 -- runtime dep (embeddings); import kept local for symmetry

    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL)
    client = OpenAI(api_key=api_key, base_url=base_url)

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        query=query,
        candidate_problem=candidate_problem,
        candidate_diff=candidate_diff[:2000],  # truncate long diffs
    )

    response = client.chat.completions.create(
        model=model,
        max_tokens=256,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )

    text = (response.choices[0].message.content or "").strip()

    # Parse the grade from the final line "GRADE: <grade>"
    grade = "irrelevant"  # safe default
    rationale = text
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("GRADE:"):
            raw_grade = line[len("GRADE:"):].strip().lower()
            if raw_grade in ("irrelevant", "related", "direct-precedent"):
                grade = raw_grade
                # rationale is everything before the GRADE line
                grade_line_idx = text.rfind(line)
                rationale = text[:grade_line_idx].strip()
            break

    return {"grade": grade, "rationale": rationale, "model": model}


def _score_gold(
    gold: list[dict],
    model: str,
    api_key: str,
    grade_fn=None,
) -> list[dict]:
    """
    Grade all gold-set rows with the given model.

    Args:
        gold: List of gold rows.
        model: Model name to pass to grade_fn.
        api_key: API key (set in environment before calling grade_fn).
        grade_fn: Callable matching grade_pair signature. Defaults to module-level
                  grade_pair. Pass a stub in tests to avoid API calls.

    Returns list of dicts with: query, human_grade, judge_grade, grade, model, rationale.
    """
    if grade_fn is None:
        grade_fn = grade_pair  # module-level; monkeypatching judge_mod.grade_pair won't work
        # Use module-level via sys.modules for monkeypatch compatibility:
        import sys
        mod = sys.modules.get(__name__)
        if mod is not None:
            grade_fn = getattr(mod, "grade_pair", grade_pair)

    os.environ["OPENAI_API_KEY"] = api_key
    results = []
    for row in gold:
        result = grade_fn(
            query=row["query"],
            candidate_problem=row["candidate_problem"],
            candidate_diff=row.get("candidate_diff", ""),
            model=model,
        )
        results.append({
            "query": row["query"],
            "human_grade": row["human_grade"],
            "judge_grade": result["grade"],
            "grade": result["grade"],  # preserve raw 3-grade
            "rationale": result["rationale"],
            "model": result["model"],
            "stratum": row.get("stratum", ""),
        })
    return results


def calibrate(
    gold: list[dict] | None = None,
    api_key: str | None = None,
) -> dict:
    """
    Run the Sonnet->Opus escalation ladder against the gold set.

    Args:
        gold: List of gold-set rows (dict with query, candidate_problem,
              candidate_diff, human_grade, stratum). If None, loads gold.jsonl.
        api_key: OPENAI_API_KEY value. If None, reads from environment.

    Returns:
        dict with keys:
          - sonnet_kappa: Cohen's kappa for Sonnet model
          - sonnet_grades: list of per-pair grade records
          - opus_kappa: (present only if Sonnet kappa < 0.6) Opus kappa
          - opus_grades: (present only if Opus invoked) list of per-pair grade records
          - opus_invoked: bool
          - verdict: "sonnet-calibrated" | "opus-calibrated" | "advisory-only"
    """
    from eval.judge.kappa import cohens_kappa  # noqa: PLC0415

    if gold is None:
        gold = [
            json.loads(line)
            for line in GOLD_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY", "")

    # Step 1: Score with Sonnet
    sonnet_grades = _score_gold(gold, SONNET_MODEL, api_key)

    # Build kappa pairs (judge_grade, human_grade) in raw 3-grade
    sonnet_pairs = [(r["judge_grade"], r["human_grade"]) for r in sonnet_grades]
    sonnet_kappa = cohens_kappa(sonnet_pairs)

    result: dict = {
        "provenance": _provenance(),
        "kappa_floor": KAPPA_FLOOR,
        "kappa_metric": "multi-category Cohen's kappa (no collapse) over the raw 3-grade scale",
        "sonnet_kappa": sonnet_kappa,
        "sonnet_grades": sonnet_grades,
        "opus_invoked": False,
    }

    if sonnet_kappa >= KAPPA_FLOOR:
        result["verdict"] = "sonnet-calibrated"
        return result

    # Step 2: Escalate to Opus
    opus_grades = _score_gold(gold, OPUS_MODEL, api_key)
    opus_pairs = [(r["judge_grade"], r["human_grade"]) for r in opus_grades]
    opus_kappa = cohens_kappa(opus_pairs)

    result["opus_kappa"] = opus_kappa
    result["opus_grades"] = opus_grades
    result["opus_invoked"] = True

    if opus_kappa >= KAPPA_FLOOR:
        result["verdict"] = "opus-calibrated"
    else:
        result["verdict"] = "advisory-only"

    return result


def _load_gold() -> list[dict]:
    """Load gold.jsonl from the default path."""
    return [
        json.loads(line)
        for line in GOLD_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_results(calibration: dict) -> None:
    """Write calibration results to calibration_results.json."""
    RESULTS_PATH.write_text(
        json.dumps(calibration, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    print(f"Calibration complete. verdict={calibration['verdict']}")
    print(f"  Sonnet kappa={calibration['sonnet_kappa']:.3f}")
    if calibration.get("opus_invoked"):
        print(f"  Opus kappa={calibration.get('opus_kappa', 'N/A')}")
    print(f"  Results written to {RESULTS_PATH}")


if __name__ == "__main__":
    # Allow `python eval/judge/judge.py` (script form) as well as
    # `python -m eval.judge.judge`: when run as a script, the repo root is not
    # on sys.path, so the lazy `from eval.judge.kappa import ...` would fail.
    _REPO_ROOT = str(pathlib.Path(__file__).resolve().parent.parent.parent)
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

    load_dotenv(".env")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set. Cannot run live calibration.", file=sys.stderr)
        sys.exit(1)

    gold = _load_gold()
    print(f"Loaded {len(gold)} gold pairs from {GOLD_PATH}")

    calibration = calibrate(gold=gold, api_key=api_key)
    _write_results(calibration)
