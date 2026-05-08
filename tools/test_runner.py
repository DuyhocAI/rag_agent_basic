# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
rag_agent/tools/test_runner.py
Generates and executes pytest tests for agent-produced code.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

logger = logging.getLogger("rag_agent.test_runner")

# A simple smoke-test template that is written alongside main.py
_TEST_TEMPLATE = '''\
"""Auto-generated smoke tests for main.py"""
import importlib.util
import sys
import os
from pathlib import Path

# Ensure the project directory is on sys.path
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))


def _load_main():
    spec = importlib.util.spec_from_file_location("main", PROJECT_DIR / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_main_importable():
    """main.py must be importable without errors."""
    _load_main()


def test_metrics_written():
    """After running main.py the metrics.json file must exist."""
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, str(PROJECT_DIR / "main.py")],
        capture_output=True, text=True, timeout=300,
        cwd=str(PROJECT_DIR),
    )
    assert result.returncode == 0, (
        f"main.py exited with code {result.returncode}\\n"
        f"STDOUT:\\n{result.stdout[-2000:]}\\n"
        f"STDERR:\\n{result.stderr[-2000:]}"
    )
    metrics_path = PROJECT_DIR / "metrics.json"
    assert metrics_path.exists(), "metrics.json was not written by main.py"
    with open(metrics_path) as f:
        metrics = json.load(f)
    assert isinstance(metrics, dict), "metrics.json must contain a JSON object"


import json  # noqa: E402  (needed inside test body above)
'''


class TestRunner:
    """
    Writes a test file next to main.py and runs pytest against it.
    Returns a result dict with keys: passed, output, test_code, returncode.
    """

    def run(self, code: str, project_dir: Path) -> dict[str, Any]:
        test_path = project_dir / "tests" / "test_main.py"
        test_path.parent.mkdir(parents=True, exist_ok=True)

        test_code = _TEST_TEMPLATE
        test_path.write_text(test_code, encoding="utf-8")
        logger.info("Running tests in %s", project_dir)

        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                str(test_path),
                "-v", "--tb=short", "--timeout=300",
                "--json-report", f"--json-report-file={project_dir / 'test_report.json'}",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_dir),
            timeout=360,
        )

        output = result.stdout + "\n" + result.stderr
        passed = result.returncode == 0
        logger.info("Tests %s | returncode=%d", "PASSED" if passed else "FAILED", result.returncode)

        # Try to load structured report if available
        report_path = project_dir / "test_report.json"
        report: dict = {}
        if report_path.exists():
            try:
                with open(report_path) as f:
                    report = json.load(f)
            except Exception:  # noqa: BLE001
                pass

        return {
            "passed": passed,
            "output": output,
            "test_code": test_code,
            "returncode": result.returncode,
            "report": report,
        }
