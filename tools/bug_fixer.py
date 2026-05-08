# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
rag_agent/tools/bug_fixer.py
Uses the LLM to repair code that failed automated tests.
"""

import logging
import re

logger = logging.getLogger("rag_agent.bug_fixer")

_SYSTEM_PROMPT = """You are an expert Python debugger.
You will receive:
  1. A Python script that produced errors or failed tests.
  2. The test output / error message.
  3. The original task description.

Your job is to return a CORRECTED version of the Python script that:
  - Fixes all the errors shown in the test output.
  - Still fulfils the original task.
  - Remains self-contained and runnable with `python main.py`.

Output ONLY the raw Python code, no markdown fences, no explanations.
"""


class BugFixer:
    def __init__(self, client, model: str, max_tokens: int):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def fix(self, code: str, test_output: str, task: str) -> str:
        """
        Given the failing *code*, *test_output*, and original *task*,
        return a corrected version of the code.
        """
        # Truncate very long test output to stay within context limits
        truncated_output = test_output[-3000:] if len(test_output) > 3000 else test_output

        user_content = (
            f"Original task:\n{task}\n\n"
            f"Failing code:\n```python\n{code}\n```\n\n"
            f"Test output / error:\n{truncated_output}"
        )

        logger.info("Invoking BugFixer | model=%s", self.model)

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )

        raw = response.choices[0].message.content or ""
        fixed = _strip_fences(raw)
        logger.info("BugFixer returned %d chars", len(fixed))
        return fixed if fixed else code  # fall back to original if response empty


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:python)?\n(.*?)```$", text, re.DOTALL)
    return match.group(1).strip() if match else text
