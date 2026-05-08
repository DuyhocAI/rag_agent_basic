# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====


from pathlib import Path
import json
import subprocess
import sys
import re


ROOT = Path(".").resolve()

EXPECTED_DIRS = [
    "agent_core",
    "agent_runtime",
    "agent_runners",
    "agent_diagnostics",
    "agent_tests",
    "agent_server",
    "logs",
    "reports",
    "backup",
    "memory",
]

EXPECTED_ROOT_WRAPPERS = [
    "agent_runners/agent.py",
    "cli.py",
    "agent_runners/cli_supervised.py",
    "server.py",
    "agent_runners/run_agent_task.py",
    "agent_runners/continue_agent.py",
    "agent_diagnostics/status.py",
]

PACKAGE_DIRS = [
    "agent_core",
    "agent_runtime",
    "agent_runners",
    "agent_diagnostics",
    "agent_tests",
    "agent_server",
]


def check_dirs():
    # Kiem tra folder mong doi
    return {
        d: (ROOT / d).exists() and (ROOT / d).is_dir()
        for d in EXPECTED_DIRS
    }


def check_wrappers():
    # Kiem tra wrapper root
    return {
        f: (ROOT / f).exists() and (ROOT / f).is_file()
        for f in EXPECTED_ROOT_WRAPPERS
    }


def check_init_files():
    # Kiem tra __init__.py
    return {
        d: (ROOT / d / "__init__.py").exists()
        for d in PACKAGE_DIRS
    }


def py_compile_all():
    # Compile tat ca Python files
    results = {}

    for path in ROOT.rglob("*.py"):
        if "backup" in path.parts:
            continue

        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )

        results[str(path)] = {
            "ok": result.returncode == 0,
            "stderr": result.stderr
        }

    return results


def scan_bad_imports():
    # Tim import cu con sot
    bad_patterns = [
        r"from agent_core.agent_tools import",
        r"import agent_core.agent_tools as agent_tools",
        r"from agent_core.agent_self_debate import",
        r"from agent_core.agent_task_validator import",
        r"from agent_core.agent_completion_guard import",
        r"from agent_core.agent_task_state import",
        r"from agent_core.agent_done_certificate import",
        r"from agent_core.agent_memory_store import",
        r"from agent_core.agent_model_client import",
        r"from agent_core.agent_endpoint_detector import",
        r"from agent_core.agent_server_manager import",
        r"from agent_core.agent_strict_tool_policy import",
        r"from agent_core.agent_intent_fallback import",
        r"from agent_core.agent_stop_signal import",
        r"from agent_runtime.agent_runtime_bridge import",
        r"from agent_runtime.agent_autonomous_controller import",
        r"from agent_diagnostics.cli_need_next_action import",
    ]

    findings = []

    for path in ROOT.rglob("*.py"):
        if "backup" in path.parts:
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")

        for pattern in bad_patterns:
            if re.search(pattern, text):
                findings.append({
                    "file": str(path),
                    "pattern": pattern
                })

    return findings


def scan_bad_script_paths():
    # Tim script paths cu con sot
    old_scripts = [
        "agent_runners/run_task_autonomous_once.py",
        "agent_runners/run_supervised_task.py",
        "agent_runners/run_agent_task.py",
        "agent_runners/agent_watchdog.py",
        "agent_runners/monitor_agent.py",
        "agent_diagnostics/doctor_auto_fix.py",
        "agent_diagnostics/auto_fix_agent_from_diagnosis.py",
        "agent_diagnostics/detect_server_endpoint.py",
        "agent_diagnostics/agent_doctor.py",
        "agent_diagnostics/cli_need_next_action.py",
        "agent_diagnostics/audit_stop_condition.py",
    ]

    findings = []

    for path in ROOT.rglob("*.py"):
        if "backup" in path.parts:
            continue

        # Bo qua wrapper root vi co the co target path moi
        text = path.read_text(encoding="utf-8", errors="ignore")

        for script in old_scripts:
            if f'"{script}"' in text or f"'{script}'" in text:
                findings.append({
                    "file": str(path),
                    "script": script
                })

    return findings


def main():
    # Chay verify
    report = {
        "cwd": str(ROOT),
        "dirs": check_dirs(),
        "wrappers": check_wrappers(),
        "init_files": check_init_files(),
        "compile": py_compile_all(),
        "bad_imports": scan_bad_imports(),
        "bad_script_paths": scan_bad_script_paths(),
    }

    Path("reports").mkdir(exist_ok=True)
    Path("reports/project_layout_verify.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("=== PROJECT LAYOUT VERIFY ===")
    print("CWD:", report["cwd"])

    print("\nDIRS:")
    print(json.dumps(report["dirs"], ensure_ascii=False, indent=2))

    print("\nWRAPPERS:")
    print(json.dumps(report["wrappers"], ensure_ascii=False, indent=2))

    print("\nINIT FILES:")
    print(json.dumps(report["init_files"], ensure_ascii=False, indent=2))

    failed_compile = {
        k: v for k, v in report["compile"].items()
        if not v["ok"]
    }

    print("\nFAILED COMPILE:")
    print(json.dumps(failed_compile, ensure_ascii=False, indent=2))

    print("\nBAD IMPORTS:")
    print(json.dumps(report["bad_imports"], ensure_ascii=False, indent=2))

    print("\nBAD SCRIPT PATHS:")
    print(json.dumps(report["bad_script_paths"], ensure_ascii=False, indent=2))

    ok = (
        all(report["dirs"].values())
        and all(report["wrappers"].values())
        and all(report["init_files"].values())
        and not failed_compile
        and not report["bad_imports"]
    )

    if ok:
        print("\nVERIFY OK")
        return 0

    print("\nVERIFY FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
