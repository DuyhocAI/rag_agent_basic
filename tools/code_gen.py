# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
rag_agent/tools/code_gen.py
Generates Python code from a natural-language task description,
optionally augmented with RAG context.
"""

import logging
import re

logger = logging.getLogger("rag_agent.code_gen")

_SYSTEM_PROMPT = """You are an expert Python AI/ML engineer.
Given a task description (and optional context from similar past projects),
write complete, runnable Python code that fulfils the task.

Rules:
- Output ONLY the raw Python code, no markdown fences, no explanations.
- The code must be self-contained and runnable with `python main.py`.
- Use standard ML libraries (PyTorch, scikit-learn, numpy, pandas, matplotlib).
- Save artefacts to paths relative to the working directory:
    models/<name>.pt, visualizations/<name>.png, metrics.json, training_history.json
- Include a `if __name__ == "__main__":` guard.
- Write clean, well-commented code.
"""


class CodeGenerator:
    def __init__(self, client, model: str, max_tokens: int):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def generate(self, task: str, context: str = "") -> str:
        """
        Generate Python code for *task*.
        *context* is optional text from the RAG store (similar past code).
        """
        user_content = f"Task:\n{task}"
        if context:
            user_content += f"\n\nRelevant context from past projects:\n{context}"

        logger.info("Generating code | model=%s task_len=%d", self.model, len(task))

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )

        raw = response.choices[0].message.content or ""
        code = _strip_fences(raw)
        logger.info("Code generated — %d chars", len(code))
        return code


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    """Remove markdown code fences if the LLM added them despite instructions."""
    text = text.strip()
    # ```python ... ``` or ``` ... ```
    pattern = r"^```(?:python)?\n(.*?)```$"
    match = re.match(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text
