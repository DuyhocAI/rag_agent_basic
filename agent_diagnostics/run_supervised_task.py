# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====


import subprocess
import sys
import time
import json
from pathlib import Path


DONE_CERTIFICATE = Path("memory/task_done_certificate.json")
ACTIVE_TASK = Path("memory/active_task_state.json")
SUPERVISOR_LOG = Path("supervisor_task.log")


def log(message):
    # Ghi log supervisor
    text = str(message)
    print(text)

    with open(SUPERVISOR_LOG, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def clear_old_done_certificate():
    # Xoa certificate cu truoc khi chay task moi
    if DONE_CERTIFICATE.exists():
        DONE_CERTIFICATE.unlink()
        log("Da xoa DoneCertificate cu")


def load_certificate():
    # Doc DoneCertificate neu co
    if not DONE_CERTIFICATE.exists():
        return None

    try:
        return json.loads(DONE_CERTIFICATE.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        return {
            "read_error": str(e)
        }


def has_done_certificate():
    # Kiem tra task da verified complete chua
    cert = load_certificate()
    return isinstance(cert, dict) and cert.get("verified_complete") is True


def build_task_from_argv():
    # Lay task tu argv hoac input
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:]).strip()

    return (input("Task> ") or "").strip()


def run_once(task):
    # Chay mot lan autonomous runner
    cmd = [
        sys.executable,
        "agent_runners/run_task_autonomous_once.py",
        task
    ]

    log("")
    log("=" * 100)
    log("SUPERVISOR RUN ONCE")
    log("=" * 100)
    log("CMD: " + " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=900
    )

    log("RETURNCODE: " + str(result.returncode))

    stdout_tail = "\n".join(result.stdout.splitlines()[-200:])
    stderr_tail = "\n".join(result.stderr.splitlines()[-200:])

    log("--- STDOUT TAIL ---")
    log(stdout_tail)

    log("--- STDERR TAIL ---")
    log(stderr_tail)

    Path("supervisor_last_stdout.log").write_text(
        result.stdout,
        encoding="utf-8"
    )

    Path("supervisor_last_stderr.log").write_text(
        result.stderr,
        encoding="utf-8"
    )

    return result.returncode


def main():
    # Chay task duoi supervisor cho den khi co DoneCertificate hoac user Ctrl+Q
    task = build_task_from_argv()

    if not task:
        log("Task rong. Thoat.")
        return 1

    if not Path("agent_runners/run_task_autonomous_once.py").exists():
        log("Khong tim thay run_task_autonomous_once.py")
        return 2

    SUPERVISOR_LOG.write_text("", encoding="utf-8")

    clear_old_done_certificate()

    max_restarts = 10
    restart_count = 0

    log("=== SUPERVISED TASK START ===")
    log("Task:")
    log(task)
    log("Supervisor se chi dung thanh cong khi co memory/task_done_certificate.json")
    log("Nhan Ctrl+C de dung khan cap. Ctrl+Q duoc xu ly trong agent loop neu terminal ho tro.")

    while True:
        if has_done_certificate():
            log("")
            log("=== TASK DONE CERTIFICATE FOUND ===")
            log(json.dumps(load_certificate(), ensure_ascii=False, indent=2))
            log("SUPERVISOR ket luan task da hoan thanh.")
            return 0

        if restart_count >= max_restarts:
            log("")
            log("=== SUPERVISOR MAX RESTARTS REACHED ===")
            log(f"restart_count={restart_count}")
            log("Task chua co DoneCertificate nen KHONG coi la done.")
            if ACTIVE_TASK.exists():
                log("Co active_task_state.json. Lan sau co the resume.")
            return 3

        restart_count += 1

        log("")
        log(f"=== SUPERVISOR ITERATION {restart_count}/{max_restarts} ===")

        try:
            code = run_once(task)
        except KeyboardInterrupt:
            log("")
            log("Supervisor nhan KeyboardInterrupt. Dung theo yeu cau nguoi dung.")
            return 130
        except subprocess.TimeoutExpired:
            log("run_task_autonomous_once.py timeout. Se thu restart neu chua co certificate.")
            code = 124
        except Exception as e:
            log(f"Loi supervisor khi run_once: {e}")
            code = 125

        if has_done_certificate():
            log("")
            log("=== TASK DONE AFTER RUN ===")
            log(json.dumps(load_certificate(), ensure_ascii=False, indent=2))
            return 0

        log("")
        log("Chua co DoneCertificate sau lan chay nay.")

        if ACTIVE_TASK.exists():
            log("Phat hien active_task_state.json. Lan chay tiep theo se resume.")
        else:
            log("Khong co active_task_state.json. Agent co the da dung sai hoac crash truoc khi checkpoint.")

        # Neu runner bao stopped_by_user thi khong restart lien tuc
        if code == 130:
            log("Runner bao stopped_by_user/interrupt. Supervisor dung.")
            return 130

        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
