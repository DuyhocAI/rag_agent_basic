# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====


from pathlib import Path
import json
import re


LOG_FILES = [
    "cli.log",
    "server.log",
    "supervisor_task.log",
    "supervisor_last_stdout.log",
    "supervisor_last_stderr.log",
    "agent_auto_runtime.log",
    "agent_stdout_runtime.log",
    "quick_test_stdout.log",
    "quick_test_stderr.log",
    "reproduce_last_failure_stdout.log",
    "reproduce_last_failure_stderr.log",
]

STATE_FILES = {
    "active_task": "memory/active_task_state.json",
    "done_certificate": "memory/task_done_certificate.json",
    "last_session": "memory/last_autonomous_session.json",
    "last_task_result": "last_task_result.json",
}

TARGET_FILE = "requirements.tft"


def read_text(path):
    # Doc file text an toan
    path = Path(path)

    if not path.exists():
        return ""

    return path.read_text(encoding="utf-8", errors="ignore")


def read_json(path):
    # Doc file JSON an toan
    path = Path(path)

    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        return {
            "read_error": str(e),
            "raw": path.read_text(encoding="utf-8", errors="ignore")[:4000]
        }


def tail(text, n=80):
    # Lay n dong cuoi
    return "\n".join(str(text).splitlines()[-n:])


def collect_all_text():
    # Gom tat ca log thanh mot text
    parts = []

    for file in LOG_FILES:
        text = read_text(file)
        if text:
            parts.append(f"\n===== {file} =====\n{text}")

    return "\n".join(parts)


def analyze():
    # Phan tich trang thai hien tai
    all_text = collect_all_text()
    low = all_text.lower()

    active = read_json(STATE_FILES["active_task"])
    cert = read_json(STATE_FILES["done_certificate"])
    last_session = read_json(STATE_FILES["last_session"])
    last_task_result = read_json(STATE_FILES["last_task_result"])

    target_path = Path(TARGET_FILE)
    target_content = ""
    if target_path.exists():
        target_content = target_path.read_text(encoding="utf-8", errors="ignore")

    signals = {
        "has_done_certificate": cert is not None and cert.get("verified_complete") is True,
        "has_active_task": active is not None,
        "target_exists": target_path.exists(),
        "target_has_required_content": all(pkg in target_content for pkg in ["requests", "numpy", "pandas"]),

        "log_mentions_target": TARGET_FILE.lower() in low,
        "log_has_tool_json": '"tool"' in all_text or "'tool'" in all_text,
        "log_has_write_file": "write_file" in low,
        "log_has_read_file": "read_file" in low,
        "log_has_tool_execution": (
            "tool execution" in low
            or "agent tool execution result" in low
            or "agent stdout tool execution result" in low
            or "fallback_execution" in low
            or "intent fallback" in low
        ),
        "log_has_self_debate": "self-debate" in low or "self_debate" in low,
        "log_has_completion_guard": "completion guard" in low or "completionguard" in low,
        "log_has_allow_stop_true": '"allow_stop": true' in low,
        "log_has_allow_stop_false": '"allow_stop": false' in low,
        "log_has_task_verified_complete": "task verified complete" in low,
        "log_has_done_certificate_issued": "done certificate issued" in low,

        "log_has_connection_error": (
            "connection refused" in low
            or "connectionerror" in low
            or "failed to establish" in low
            or "max retries exceeded" in low
            or "connecttimeout" in low
            or "readtimeout" in low
        ),
        "log_has_404": "404" in low or "not found" in low,
        "log_has_500": "500" in low or "internal server error" in low,
        "log_has_timeout": "timeout" in low,
        "log_has_traceback": "traceback" in low,
        "log_has_none_strip": "nonetype" in low and "strip" in low,
        "log_has_exit": re.search(r"(^|\n)\s*exit\s*($|\n)", all_text, re.IGNORECASE) is not None,
        "log_has_legacy_cli": "legacy cli" in low,
        "log_has_supervisor_max_restarts": "supervisor max restarts reached" in low,
    }

    return {
        "signals": signals,
        "active": active,
        "cert": cert,
        "last_session": last_session,
        "last_task_result": last_task_result,
        "target_content": target_content,
        "all_text": all_text
    }


def infer_current_task(state):
    # Suy luan task hien tai tu active state/session/result/log
    active = state["active"]
    last_session = state["last_session"]
    last_task_result = state["last_task_result"]

    if isinstance(active, dict) and active.get("task"):
        return active.get("task")

    if isinstance(last_session, dict) and last_session.get("task"):
        return last_session.get("task")

    if isinstance(last_task_result, dict) and last_task_result.get("task"):
        return last_task_result.get("task")

    return (
        "Hãy tạo file requirements.tft trong thư mục hiện tại với nội dung "
        "requests, numpy, pandas. Sau đó đọc lại file để xác nhận."
    )


def decide_next_action(state):
    # Dua ra hanh dong tiep theo
    s = state["signals"]
    task = infer_current_task(state)

    actions = []
    reasons = []

    if s["has_done_certificate"]:
        return {
            "status": "DONE",
            "reasons": [
                "Da co memory/task_done_certificate.json verified_complete=true."
            ],
            "actions": [
                "Khong can tiep tuc task hien tai.",
                "Co the bat dau task moi bang: python cli_supervised.py"
            ]
        }

    if s["log_has_connection_error"] or s["log_has_404"] or s["log_has_500"]:
        reasons.append("Co loi server/endpoint trong log.")
        actions.append("Chay server neu chua chay: python server.py")
        actions.append("Detect endpoint: python detect_server_endpoint.py")
        actions.append("Cap nhat agent_model_config.json theo endpoint dung.")
        return {
            "status": "NEEDS_SERVER_OR_ENDPOINT_FIX",
            "reasons": reasons,
            "actions": actions
        }

    if s["log_has_none_strip"]:
        reasons.append("Co loi NoneType.strip trong log.")
        actions.append("Patch .strip() thanh (value or '').strip() trong cli/run scripts.")
        actions.append("Chay: python doctor_auto_fix.py")
        return {
            "status": "NEEDS_CODE_PATCH",
            "reasons": reasons,
            "actions": actions
        }

    if s["has_active_task"]:
        reasons.append("Co memory/active_task_state.json, task dang do dang.")
        actions.append('Resume bang supervisor:')
        actions.append(f'python run_agent_task.py "{task}"')
        return {
            "status": "NEEDS_RESUME",
            "reasons": reasons,
            "actions": actions
        }

    if s["target_exists"] and s["target_has_required_content"]:
        if not s["log_has_read_file"]:
            reasons.append("File requirements.tft da co noi dung dung, nhung log chua thay read_file xac nhan.")
            actions.append('Chay task xac nhan bang read_file qua supervisor:')
            actions.append(f'python run_agent_task.py "{task}"')
            return {
                "status": "NEEDS_READ_VERIFY",
                "reasons": reasons,
                "actions": actions
            }

        if not s["log_has_completion_guard"]:
            reasons.append("File co va da co read_file, nhung chua thay CompletionGuard/DoneCertificate.")
            actions.append("Chay lai qua autonomous supervisor de self-debate va issue DoneCertificate.")
            actions.append(f'python run_agent_task.py "{task}"')
            return {
                "status": "NEEDS_COMPLETION_GUARD",
                "reasons": reasons,
                "actions": actions
            }

        if s["log_has_allow_stop_true"] and not s["has_done_certificate"]:
            reasons.append("CompletionGuard co allow_stop=true nhung chua co DoneCertificate.")
            actions.append("Can patch DoneCertificate trong controller.")
            actions.append("Chay: python audit_stop_condition.py")
            actions.append("Chay lai: python run_agent_task.py \"<task>\"")
            return {
                "status": "NEEDS_DONE_CERTIFICATE_PATCH",
                "reasons": reasons,
                "actions": actions
            }

    if s["log_mentions_target"] and not s["log_has_tool_json"]:
        reasons.append("Log co request requirements.tft nhung model khong sinh JSON tool call.")
        actions.append("Can strict tool policy hoac intent fallback.")
        actions.append("Chay: python doctor_auto_fix.py")
        actions.append(f'Sau do chay lai: python run_agent_task.py "{task}"')
        return {
            "status": "NEEDS_TOOL_JSON",
            "reasons": reasons,
            "actions": actions
        }

    if s["log_has_tool_json"] and not s["log_has_tool_execution"]:
        reasons.append("Co JSON tool call nhung khong co tool execution.")
        actions.append("Dang can executor/supervised runner.")
        actions.append(f'Chay: python run_agent_task.py "{task}"')
        return {
            "status": "NEEDS_TOOL_EXECUTOR",
            "reasons": reasons,
            "actions": actions
        }

    if s["log_has_write_file"] and not s["target_exists"]:
        reasons.append("Log co write_file nhung file khong ton tai.")
        actions.append("Kiem tra workspace/path trong tool execution.")
        actions.append("Chay: python read_latest_failure.py")
        actions.append(f'Chay lai qua supervisor: python run_agent_task.py "{task}"')
        return {
            "status": "NEEDS_WORKSPACE_OR_EXECUTION_FIX",
            "reasons": reasons,
            "actions": actions
        }

    if s["log_has_allow_stop_false"]:
        reasons.append("CompletionGuard allow_stop=false, task chua duoc phep dung.")
        actions.append(f'Chay tiep/resume: python run_agent_task.py "{task}"')
        return {
            "status": "NEEDS_CONTINUE_AFTER_GUARD_FALSE",
            "reasons": reasons,
            "actions": actions
        }

    if s["log_has_timeout"]:
        reasons.append("Process bi timeout.")
        actions.append("Chay lai qua supervisor de resume.")
        actions.append(f'python run_agent_task.py "{task}"')
        return {
            "status": "NEEDS_RESUME_AFTER_TIMEOUT",
            "reasons": reasons,
            "actions": actions
        }

    if s["log_has_exit"]:
        reasons.append("Log co lenh exit, co the test/CLI da thoat som.")
        actions.append("Khong gui exit trong autonomous task.")
        actions.append(f'Chay: python run_agent_task.py "{task}"')
        return {
            "status": "NEEDS_RERUN_WITHOUT_EXIT",
            "reasons": reasons,
            "actions": actions
        }

    reasons.append("Chua co DoneCertificate va khong du dau hieu de xac dinh thieu buoc nao.")
    actions.append("Chay doctor de lay chan doan day du: python agent_doctor.py")
    actions.append(f'Chay task qua supervisor: python run_agent_task.py "{task}"')
    return {
        "status": "UNKNOWN_NEEDS_SUPERVISED_RUN",
        "reasons": reasons,
        "actions": actions
    }


def print_state_summary(state):
    # In tom tat trang thai
    s = state["signals"]

    print("=" * 100)
    print("CLI NEED NEXT ACTION")
    print("=" * 100)

    print("\nSIGNALS:")
    print(json.dumps(s, ensure_ascii=False, indent=2))

    print("\nCURRENT TASK:")
    print(infer_current_task(state))

    print("\nTARGET FILE:")
    print("requirements.tft exists:", s["target_exists"])
    if s["target_exists"]:
        print(state["target_content"])

    print("\nDONE CERTIFICATE:")
    if state["cert"]:
        print(json.dumps(state["cert"], ensure_ascii=False, indent=2))
    else:
        print("Khong co DoneCertificate")

    print("\nACTIVE TASK:")
    if state["active"]:
        active_brief = {
            "status": state["active"].get("status"),
            "iteration": state["active"].get("iteration"),
            "timestamp": state["active"].get("timestamp"),
            "task": state["active"].get("task"),
            "recent_tool_results_count": len(state["active"].get("recent_tool_results", []) or []),
            "messages_count": len(state["active"].get("messages", []) or []),
        }
        print(json.dumps(active_brief, ensure_ascii=False, indent=2))
    else:
        print("Khong co active task")


def print_log_tails():
    # In tail log can thiet
    important = [
        "supervisor_task.log",
        "supervisor_last_stdout.log",
        "supervisor_last_stderr.log",
        "cli.log",
        "server.log",
    ]

    print("\n" + "=" * 100)
    print("IMPORTANT LOG TAILS")
    print("=" * 100)

    for file in important:
        text = read_text(file)

        print("\n" + "-" * 100)
        print(file)
        print("-" * 100)

        if not text:
            print("Khong ton tai hoac rong")
        else:
            print(tail(text, 80))


def main():
    # Main
    state = analyze()
    decision = decide_next_action(state)

    print_state_summary(state)

    print("\n" + "=" * 100)
    print("DECISION")
    print("=" * 100)
    print(json.dumps(decision, ensure_ascii=False, indent=2))

    print("\nNEXT COMMANDS:")
    for action in decision["actions"]:
        print("- " + action)

    print_log_tails()


if __name__ == "__main__":
    main()
