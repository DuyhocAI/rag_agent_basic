# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====


import json
import subprocess
import sys
import time
import threading
from pathlib import Path

from agent_diagnostics.cli_need_next_action import analyze, decide_next_action, infer_current_task


class CtrlQStopper:
    def __init__(self):
        # Co dung khi nguoi dung nhan Ctrl+Q
        self.stop_requested = False
        self.reason = None

    def start(self):
        # Bat dau lang nghe Ctrl+Q
        thread = threading.Thread(target=self._listen, daemon=True)
        thread.start()

    def request_stop(self, reason):
        # Yeu cau dung watchdog
        self.stop_requested = True
        self.reason = reason

    def _listen(self):
        # Lang nghe Ctrl+Q tren Windows/Unix
        if sys.platform.startswith("win"):
            self._listen_windows()
        else:
            self._listen_unix()

    def _listen_windows(self):
        # Bat Ctrl+Q tren Windows
        try:
            import msvcrt

            while not self.stop_requested:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()

                    # Ctrl+Q = ASCII 17
                    if ch == "\x11":
                        self.request_stop("Nguoi dung nhan Ctrl+Q")
                        return

                time.sleep(0.05)

        except Exception as e:
            self.request_stop(f"Loi Ctrl+Q listener Windows: {e}")

    def _listen_unix(self):
        # Bat Ctrl+Q tren Unix/Linux/macOS
        try:
            import termios
            import tty
            import select

            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)

            try:
                tty.setcbreak(fd)

                while not self.stop_requested:
                    ready, _, _ = select.select([sys.stdin], [], [], 0.05)

                    if ready:
                        ch = sys.stdin.read(1)

                        if ch == "\x11":
                            self.request_stop("Nguoi dung nhan Ctrl+Q")
                            return

            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        except Exception:
            # Neu terminal khong cho bat Ctrl+Q thi bo qua
            pass


def log(message):
    # Ghi log watchdog
    text = str(message)
    print(text)

    with open("agent_watchdog.log", "a", encoding="utf-8") as f:
        f.write(text + "\n")


def run_command(cmd, timeout):
    # Chay command con va ghi log tail
    log("")
    log("=" * 100)
    log("RUN COMMAND")
    log("=" * 100)
    log(" ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout
        )

        Path("agent_watchdog_last_stdout.log").write_text(
            result.stdout,
            encoding="utf-8"
        )

        Path("agent_watchdog_last_stderr.log").write_text(
            result.stderr,
            encoding="utf-8"
        )

        log(f"RETURNCODE: {result.returncode}")
        log("--- STDOUT TAIL ---")
        log("\n".join(result.stdout.splitlines()[-200:]))
        log("--- STDERR TAIL ---")
        log("\n".join(result.stderr.splitlines()[-100:]))

        return result.returncode

    except subprocess.TimeoutExpired as e:
        log("COMMAND TIMEOUT")
        log(f"stdout: {e.stdout}")
        log(f"stderr: {e.stderr}")
        return 124

    except Exception as e:
        log(f"COMMAND ERROR: {e}")
        return 125


def ensure_server_or_endpoint():
    # Thu sua server/endpoint neu co script ho tro
    if Path("agent_diagnostics/detect_server_endpoint.py").exists():
        return run_command(
            [sys.executable, "agent_diagnostics/detect_server_endpoint.py"],
            timeout=120
        )

    log("Khong co detect_server_endpoint.py")
    return 2


def run_doctor_fix():
    # Chay auto-fix neu co
    if Path("agent_diagnostics/doctor_auto_fix.py").exists():
        return run_command(
            [sys.executable, "agent_diagnostics/doctor_auto_fix.py"],
            timeout=180
        )

    if Path("agent_diagnostics/auto_fix_agent_from_diagnosis.py").exists():
        return run_command(
            [sys.executable, "agent_diagnostics/auto_fix_agent_from_diagnosis.py"],
            timeout=180
        )

    log("Khong co doctor_auto_fix.py hoac auto_fix_agent_from_diagnosis.py")
    return 2


def run_supervised_task(task):
    # Chay task bang runner chuan
    if Path("agent_runners/run_agent_task.py").exists():
        return run_command(
            [sys.executable, "agent_runners/run_agent_task.py", task],
            timeout=1500
        )

    if Path("agent_runners/run_supervised_task.py").exists():
        return run_command(
            [sys.executable, "agent_runners/run_supervised_task.py", task],
            timeout=1500
        )

    log("Khong co run_agent_task.py hoac run_supervised_task.py")
    return 2


def print_decision(decision):
    # In decision hien tai
    log("")
    log("=" * 100)
    log("WATCHDOG DECISION")
    log("=" * 100)
    log(json.dumps(decision, ensure_ascii=False, indent=2))


def watchdog_loop(max_rounds=20, sleep_seconds=2):
    # Vong lap watchdog chay den khi done hoac Ctrl+Q
    stopper = CtrlQStopper()
    stopper.start()

    Path("agent_watchdog.log").write_text("", encoding="utf-8")

    log("=== AGENT WATCHDOG START ===")
    log("Nhan Ctrl+Q de dung watchdog sau vong hien tai.")

    for round_idx in range(1, max_rounds + 1):
        if stopper.stop_requested:
            log("")
            log("=== WATCHDOG STOPPED BY USER ===")
            log(stopper.reason)
            return 130

        log("")
        log("=" * 100)
        log(f"WATCHDOG ROUND {round_idx}/{max_rounds}")
        log("=" * 100)

        state = analyze()
        decision = decide_next_action(state)
        print_decision(decision)

        status = decision.get("status")
        task = infer_current_task(state)

        if status == "DONE":
            log("")
            log("=== WATCHDOG DONE ===")
            log("Da co DoneCertificate. Task da hoan thanh.")
            return 0

        if status == "NEEDS_SERVER_OR_ENDPOINT_FIX":
            ensure_server_or_endpoint()
            time.sleep(sleep_seconds)
            continue

        if status == "NEEDS_CODE_PATCH":
            run_doctor_fix()
            time.sleep(sleep_seconds)
            continue

        # Cac trang thai con lai thu chay supervisor
        runnable_statuses = {
            "NEEDS_RESUME",
            "NEEDS_READ_VERIFY",
            "NEEDS_COMPLETION_GUARD",
            "NEEDS_DONE_CERTIFICATE_PATCH",
            "NEEDS_TOOL_JSON",
            "NEEDS_TOOL_EXECUTOR",
            "NEEDS_WORKSPACE_OR_EXECUTION_FIX",
            "NEEDS_CONTINUE_AFTER_GUARD_FALSE",
            "NEEDS_RESUME_AFTER_TIMEOUT",
            "NEEDS_RERUN_WITHOUT_EXIT",
            "UNKNOWN_NEEDS_SUPERVISED_RUN",
        }

        if status in runnable_statuses:
            run_supervised_task(task)
            time.sleep(sleep_seconds)
            continue

        log(f"Khong biet xu ly status: {status}")
        time.sleep(sleep_seconds)

    log("")
    log("=== WATCHDOG MAX ROUNDS REACHED ===")
    log("Task chua done sau gioi han watchdog.")
    return 3


def main():
    # Entry point
    return watchdog_loop(max_rounds=20, sleep_seconds=2)


if __name__ == "__main__":
    raise SystemExit(main())
