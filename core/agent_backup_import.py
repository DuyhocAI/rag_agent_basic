# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
rag_agent/core/agent.py
Main RAG Agent orchestrator — Generate → Test → Fix → Improve pipeline.
"""

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from openai import OpenAI

logger = logging.getLogger("rag_agent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(config_path: str = "config.json") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _make_client(cfg: dict) -> OpenAI:
    return OpenAI(
        api_key=cfg["openai_api_key"],
        base_url=cfg.get("openai_base_url", "https://api.openai.com/v1"),
    )


# ---------------------------------------------------------------------------
# RAGAgent
# ---------------------------------------------------------------------------

class RAGAgent:
    """
    Orchestrates the full Generate → Test → Fix → Improve pipeline.

    The agent:
      1. Retrieves relevant context from the vector store (RAG).
      2. Generates code via CodeGenerator.
      3. Runs tests via TestRunner.
      4. If tests fail, delegates to BugFixer (up to max_iterations).
      5. If score < threshold, delegates to ModelImprover (up to max_iterations).
      6. Produces visualisations via Visualiser.
      7. Persists everything under projects/<name>_<ts>/.
    """

    def __init__(self, config_path: str = "config.json"):
        self.cfg = _load_config(config_path)
        self.client = _make_client(self.cfg)
        self.model = self.cfg.get("model", "claude-opus-4-6")
        self.max_tokens = self.cfg.get("max_tokens", 4096)
        self.threshold = self.cfg.get("performance_threshold", 0.80)
        self.max_iterations = self.cfg.get("max_iterations", 5)

        # Lazy imports so individual tools can be imported in isolation.
        from tools.code_gen import CodeGenerator
        from tools.test_runner import TestRunner
        from tools.evaluator import Evaluator
        from tools.bug_fixer import BugFixer
        from tools.model_improver import ModelImprover
        from tools.visualizer import Visualizer
        from rag.store import RAGStore

        self.rag = RAGStore()
        self.code_gen = CodeGenerator(self.client, self.model, self.max_tokens)
        self.test_runner = TestRunner()
        self.evaluator = Evaluator()
        self.bug_fixer = BugFixer(self.client, self.model, self.max_tokens)
        self.model_improver = ModelImprover(self.client, self.model, self.max_tokens)
        self.visualizer = Visualizer()

        logger.info("RAGAgent initialised - LLM backend: %s", self.cfg.get("llm_backend", "openai"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, task: str) -> dict:
        """
        Execute the full pipeline for *task* and return a result dict.
        """
        task_id = f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        project_dir = self._create_project_dir(task_id, task)

        logger.info("[%s] Starting pipeline | task=%r", task_id, task[:80])

        # 1. RAG context retrieval
        context_docs = self.rag.query(task, n_results=5)
        context_text = "\n\n".join(context_docs) if context_docs else ""

        # 2. Code generation
        code = self.code_gen.generate(task, context=context_text)
        self._save(project_dir / "main.py", code)

        # 3. Test → Fix loop
        test_results: dict[str, Any] = {}
        for iteration in range(1, self.max_iterations + 1):
            logger.info("[%s] Iteration %d — running tests", task_id, iteration)
            test_results = self.test_runner.run(code, project_dir)
            self._save(
                project_dir / "tests" / "test_main.py",
                test_results.get("test_code", ""),
            )

            if test_results.get("passed", False):
                logger.info("[%s] Tests passed on iteration %d", task_id, iteration)
                break

            if iteration == self.max_iterations:
                logger.warning("[%s] Max iterations reached, tests still failing", task_id)
                break

            logger.info("[%s] Tests failed — invoking BugFixer", task_id)
            code = self.bug_fixer.fix(
                code=code,
                test_output=test_results.get("output", ""),
                task=task,
            )
            self._save(project_dir / "main.py", code)

        # 4. Evaluate
        metrics = self.evaluator.evaluate(code, test_results, project_dir)
        self._save_json(project_dir / "metrics.json", metrics)

        # 5. Model-improve loop (if score below threshold)
        score = metrics.get("score", 0.0)
        for iteration in range(1, self.max_iterations + 1):
            if score >= self.threshold:
                break
            logger.info(
                "[%s] Score %.3f < threshold %.3f — invoking ModelImprover (iter %d)",
                task_id, score, self.threshold, iteration,
            )
            code = self.model_improver.improve(
                code=code,
                metrics=metrics,
                task=task,
            )
            self._save(project_dir / "main.py", code)
            test_results = self.test_runner.run(code, project_dir)
            metrics = self.evaluator.evaluate(code, test_results, project_dir)
            score = metrics.get("score", 0.0)
            self._save_json(project_dir / "metrics.json", metrics)

        # 6. Visualise
        try:
            viz_dir = project_dir / "visualizations"
            viz_dir.mkdir(parents=True, exist_ok=True)
            self.visualizer.plot_metrics(metrics, output_dir=viz_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Visualisation skipped: %s", task_id, exc)

        # 7. Store this task's code back into RAG for future retrieval
        self.rag.add(task, code)

        # 8. Save metadata
        meta = {
            "task_id": task_id,
            "task": task,
            "model": self.model,
            "score": score,
            "passed": test_results.get("passed", False),
            "project_dir": str(project_dir),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._save_json(project_dir / "project_meta.json", meta)

        logger.info(
            "[%s] Pipeline complete | score=%.3f passed=%s dir=%s",
            task_id, score, meta["passed"], project_dir,
        )
        return meta

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _create_project_dir(self, task_id: str, task: str) -> Path:
        slug = task[:40].strip().replace(" ", "_").replace("/", "-")
        ts = time.strftime("%Y%m%d_%H%M%S")
        name = f"{slug}_{ts}"
        project_dir = Path("projects") / name
        (project_dir / "tests").mkdir(parents=True, exist_ok=True)
        (project_dir / "models").mkdir(parents=True, exist_ok=True)
        (project_dir / "visualizations").mkdir(parents=True, exist_ok=True)
        self._save_json(
            project_dir / "project_meta.json",
            {"task_id": task_id, "task": task, "status": "running"},
        )
        return project_dir

    @staticmethod
    def _save(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _save_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
