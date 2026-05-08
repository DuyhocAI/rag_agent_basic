# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
rag_agent/tools/model_improver.py
Uses the LLM to refine code whose performance score is below the threshold.
"""

import logging
import re
from typing import Any

logger = logging.getLogger("rag_agent.model_improver")

_SYSTEM_PROMPT = """You are an expert ML engineer specialising in model optimisation.
You will receive:
  1. A Python ML script.
  2. Its current performance metrics.
  3. The original task description including the target metric.

Your job is to return an IMPROVED version of the script that:
  - Achieves a higher score / accuracy / R² (whichever is relevant).
  - May use better hyperparameters, architecture, regularisation, data augmentation, etc.
  - Still fulfils all requirements of the original task.
  - Saves artefacts to the same paths as before.
  - Is self-contained and runnable with `python main.py`.

Output ONLY the raw Python code, no markdown fences, no explanations.
"""


class ModelImprover:
    def __init__(self, client, model: str, max_tokens: int):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def improve(self, code: str, metrics: dict[str, Any], task: str) -> str:
        """
        Return an improved version of *code* given current *metrics* and *task*.
        """
        metrics_str = "\n".join(f"  {k}: {v}" for k, v in metrics.items())

        user_content = (
            f"Original task:\n{task}\n\n"
            f"Current metrics:\n{metrics_str}\n\n"
            f"Current code:\n```python\n{code}\n```"
        )

        logger.info("Invoking ModelImprover | model=%s | score=%.4f", self.model, metrics.get("score", 0))

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )

        raw = response.choices[0].message.content or ""
        improved = _strip_fences(raw)
        logger.info("ModelImprover returned %d chars", len(improved))
        return improved if improved else code


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:python)?\n(.*?)```$", text, re.DOTALL)
    return match.group(1).strip() if match else text
