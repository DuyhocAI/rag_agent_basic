# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
rag_agent/tools/evaluator.py
Evaluates generated code and produces a normalised performance score.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("rag_agent.evaluator")


class Evaluator:
    """
    Computes a composite performance score from:
      - test pass/fail
      - metrics.json written by main.py (accuracy, r2, mse, …)
      - code quality heuristics
    """

    def evaluate(
        self,
        code: str,
        test_results: dict[str, Any],
        project_dir: Path,
    ) -> dict[str, Any]:

        metrics: dict[str, Any] = {}

        # 1. Load metrics.json if main.py produced one
        metrics_path = project_dir / "metrics.json"
        if metrics_path.exists():
            try:
                with open(metrics_path) as f:
                    metrics = json.load(f)
                logger.info("Loaded metrics.json: %s", metrics)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not parse metrics.json: %s", exc)

        # 2. Test pass bonus
        test_passed = test_results.get("passed", False)

        # 3. Compute score
        score = _compute_score(metrics, test_passed, code)
        metrics["score"] = round(score, 4)
        metrics["test_passed"] = test_passed

        logger.info("Evaluation complete | score=%.4f", score)
        return metrics


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _compute_score(metrics: dict, test_passed: bool, code: str) -> float:
    """
    Returns a float in [0, 1].
    Priority: explicit metric from model > test pass heuristic > code quality.
    """
    # Try to extract a canonical accuracy / r2 / score metric from the dict
    for key in ("accuracy", "test_accuracy", "val_accuracy", "r2", "score"):
        val = metrics.get(key)
        if isinstance(val, (int, float)) and 0.0 <= val <= 1.0:
            base = float(val)
            # Small test-pass bonus (up to +0.05)
            bonus = 0.05 if test_passed else 0.0
            return min(1.0, base + bonus)

    # Fallback: test_passed alone gives 0.5; combined with code quality up to 0.75
    quality = _code_quality_score(code)
    if test_passed:
        return min(0.75, 0.5 + quality * 0.25)
    return quality * 0.4


def _code_quality_score(code: str) -> float:
    """Very lightweight heuristic quality score in [0, 1]."""
    if not code:
        return 0.0
    score = 0.0
    lines = code.splitlines()
    n = len(lines)

    # Length sanity (between 30 and 600 lines is healthy)
    if 30 <= n <= 600:
        score += 0.3
    elif n > 10:
        score += 0.1

    # Has __main__ guard
    if 'if __name__ == "__main__"' in code or "if __name__ == '__main__'" in code:
        score += 0.2

    # Has imports
    if re.search(r"^import |^from ", code, re.MULTILINE):
        score += 0.1

    # Has comments / docstrings
    comment_lines = sum(1 for l in lines if l.strip().startswith("#") or '"""' in l)
    if comment_lines >= 3:
        score += 0.2

    # Saves metrics.json
    if "metrics.json" in code:
        score += 0.2

    return min(1.0, score)
