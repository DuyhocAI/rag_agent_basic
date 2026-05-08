# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
rag_agent/cli.py
Live interactive CLI for the RAG Agent.

Features
--------
- Persistent memory at C:\\Bao_Duy\\rag_agent\\agent_memory.json
- Real-time terminal dashboard with progress bar & phase tracker
- Automatic retry / error-fix loop (up to MAX_FIX_RETRIES)
- Backup-before-modify: any file the agent writes is first backed up to
  <file>.bak.<timestamp> before being overwritten or deleted
- Colour output on Windows (via colorama) and ANSI terminals
- Interactive REPL mode (`python cli.py`) or one-shot commands

Usage
-----
  python cli.py                          # interactive REPL
  python cli.py run "task description"   # one-shot run with live dashboard
  python cli.py status                   # quick server status
  python cli.py task  <task_id>          # inspect a finished task
  python cli.py logs  [--lines N]        # tail the agent log
  python cli.py memory                   # dump memory file
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# -- optional colour support ---------------------------------------------------
try:
    import colorama
    colorama.init(autoreset=True)
    C_GREEN   = colorama.Fore.GREEN
    C_CYAN    = colorama.Fore.CYAN
    C_YELLOW  = colorama.Fore.YELLOW
    C_RED     = colorama.Fore.RED
    C_BLUE    = colorama.Fore.BLUE
    C_MAGENTA = colorama.Fore.MAGENTA
    C_WHITE   = colorama.Fore.WHITE
    C_BOLD    = colorama.Style.BRIGHT
    C_RESET   = colorama.Style.RESET_ALL
except ImportError:
    C_GREEN = C_CYAN = C_YELLOW = C_RED = C_BLUE = C_MAGENTA = C_WHITE = ""
    C_BOLD  = C_RESET = ""

# -- constants -----------------------------------------------------------------
MEMORY_PATH     = Path(r"C:\Bao_Duy\rag_agent\agent_memory.json")
CONFIG_PATH     = Path("config.json")
POLL_INTERVAL   = 2        # seconds between server polls
MAX_FIX_RETRIES = 5        # auto-fix attempts before giving up
PROGRESS_WIDTH  = 40       # width of the progress bar in chars

# Pipeline phases -> approximate cumulative completion %
PHASES = [
    ("Initialising",     5),
    ("RAG Retrieval",   15),
    ("Code Generation", 35),
    ("Running Tests",   55),
    ("Bug Fixing",      70),
    ("Evaluating",      80),
    ("Model Improving", 90),
    ("Visualising",     95),
    ("Saving Results", 100),
]

# -----------------------------------------------------------------------------
# Persistent memory
# -----------------------------------------------------------------------------

def _memory_load() -> dict:
    try:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        if MEMORY_PATH.exists():
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {"tasks": [], "errors": [], "stats": {"total": 0, "passed": 0, "failed": 0}}


def _memory_save(mem: dict) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if MEMORY_PATH.exists():
        _backup_file(MEMORY_PATH, silent=True)
    tmp = MEMORY_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2, ensure_ascii=False)
    tmp.replace(MEMORY_PATH)


def _memory_record_task(mem: dict, task_id: str, task: str, result: dict) -> None:
    passed = result.get("passed", False)
    mem["tasks"].append({
        "task_id":    task_id,
        "task":       task,
        "score":      result.get("score"),
        "passed":     passed,
        "project_dir":result.get("project_dir"),
        "timestamp":  _now_iso(),
    })
    mem["stats"]["total"]  += 1
    mem["stats"]["passed"] += int(passed)
    mem["stats"]["failed"] += int(not passed)
    mem["tasks"] = mem["tasks"][-200:]


def _memory_record_error(mem: dict, task_id: str, error: str) -> None:
    mem["errors"].append({"task_id": task_id, "error": error, "timestamp": _now_iso()})
    mem["errors"] = mem["errors"][-100:]

# -----------------------------------------------------------------------------
# Safe file operations (backup before write/delete)
# -----------------------------------------------------------------------------

def _backup_file(path: Path, silent: bool = False) -> Optional[Path]:
    """Copy *path* -> <path>.bak.<timestamp> before it is touched."""
    if not path.exists():
        return None
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f"{path.suffix}.bak.{ts}")
    try:
        shutil.copy2(path, backup)
        if not silent:
            _print_info(f"  Backed up -> {backup.name}")
        return backup
    except Exception as exc:
        if not silent:
            _print_warn(f"  Backup failed for {path}: {exc}")
        return None


def safe_write(path: Path, content: str) -> None:
    """Write *content* to *path*, backing up the existing file first."""
    path = Path(path)
    if path.exists():
        _backup_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def safe_delete(path: Path) -> None:
    """Delete *path*, backing it up first."""
    path = Path(path)
    if path.exists():
        _backup_file(path)
        path.unlink()

# -----------------------------------------------------------------------------
# Terminal helpers
# -----------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _print_header(text: str) -> None:
    w = 70
    print(f"\n{C_BOLD}{C_CYAN}{'=' * w}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}  {text}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}{'=' * w}{C_RESET}")


def _print_info(text: str) -> None:
    print(f"{C_CYAN}[{_now_str()}]{C_RESET} {text}")


def _print_ok(text: str) -> None:
    print(f"{C_GREEN}[{_now_str()}] {text}{C_RESET}")


def _print_warn(text: str) -> None:
    print(f"{C_YELLOW}[{_now_str()}]  {text}{C_RESET}")


def _print_err(text: str) -> None:
    print(f"{C_RED}[{_now_str()}]  {text}{C_RESET}")


def _progress_bar(pct: float, width: int = PROGRESS_WIDTH) -> str:
    pct    = max(0.0, min(100.0, pct))
    filled = int(width * pct / 100)
    bar    = "" * filled + "" * (width - filled)
    return f"{C_CYAN}[{C_GREEN}{bar}{C_CYAN}]{C_RESET} {C_BOLD}{pct:5.1f}%{C_RESET}"


def _status_colour(s: str) -> str:
    return {
        "running": C_YELLOW,
        "done":    C_GREEN,
        "error":   C_RED,
    }.get(s, C_WHITE)


# Dashboard occupies exactly DASH_LINES lines; first render uses print,
# subsequent renders rewind and overwrite.
DASH_LINES = 11
_dashboard_drawn = False


def _render_dashboard(
    task: str, task_id: str, status: str,
    phase: str, pct: float, elapsed: float,
    fix_attempt: int, last_event: str,
) -> None:
    global _dashboard_drawn
    bar   = _progress_bar(pct)
    sc    = _status_colour(status)
    lines = [
        "",
        f"{C_BOLD}{C_BLUE}   RAG AGENT  Live Dashboard {'':30}{C_RESET}",
        f"  Task    : {C_WHITE}{task[:65]}{C_RESET}",
        f"  ID      : {C_YELLOW}{task_id}{C_RESET}",
        f"  Status  : {sc}{status.upper()}{C_RESET}",
        f"  Phase   : {C_MAGENTA}{phase}{C_RESET}",
        f"  Progress: {bar}",
        f"  Elapsed : {C_WHITE}{elapsed:.0f}s{C_RESET}   Fix attempts: {C_YELLOW}{fix_attempt}/{MAX_FIX_RETRIES}{C_RESET}",
        f"  Event   : {C_WHITE}{last_event[:68]}{C_RESET}",
        f"{C_BOLD}{C_BLUE}{'':70}{C_RESET}",
        "",
    ]
    if _dashboard_drawn:
        # Move cursor up DASH_LINES and clear to end of screen
        sys.stdout.write(f"\033[{DASH_LINES}A\033[J")
    print("\n".join(lines), flush=True)
    _dashboard_drawn = True

# -----------------------------------------------------------------------------
# Config / HTTP
# -----------------------------------------------------------------------------

def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        _print_err("config.json not found. Run from the rag_agent/ directory.")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _base_url(cfg: dict) -> str:
    host = cfg.get("server", {}).get("host", "127.0.0.1")
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    port = cfg.get("server", {}).get("port", 8765)
    return f"http://{host}:{port}"


def _http_get(url: str, timeout: int = 10) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _http_post(url: str, payload: dict, timeout: int = 10) -> Optional[dict]:
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _wait_for_server(base: str, retries: int = 5) -> bool:
    for i in range(retries):
        if _http_get(f"{base}/", timeout=4):
            return True
        _print_warn(f"Server not reachable -- retrying ({i+1}/{retries}) ...")
        time.sleep(3)
    return False

# -----------------------------------------------------------------------------
# Phase inference from log lines
# -----------------------------------------------------------------------------

def _infer_phase(log_lines: list[str]) -> tuple[str, float]:
    text = " ".join(log_lines[-20:]).lower()
    if "saving results" in text or "project_meta" in text:
        return "Saving Results", 100.0
    if "visuali" in text:
        return "Visualising", 95.0
    if "modelimprover" in text or "model_improver" in text:
        return "Model Improving", 88.0
    if "evaluat" in text or "score" in text:
        return "Evaluating", 80.0
    if "bugfix" in text or "bug_fixer" in text or "fixing" in text:
        return "Bug Fixing", 68.0
    if "test" in text or "pytest" in text:
        return "Running Tests", 55.0
    if "generating code" in text or "code_gen" in text:
        return "Code Generation", 35.0
    if "rag" in text or "retriev" in text:
        return "RAG Retrieval", 15.0
    return "Initialising", 5.0

# -----------------------------------------------------------------------------
# Core run command -- live dashboard + auto-fix
# -----------------------------------------------------------------------------

def cmd_run(base: str, task: str) -> None:
    global _dashboard_drawn
    _dashboard_drawn = False
    mem = _memory_load()

    _print_header(f"New Task  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _print_info(f"Task: {task}")

    # Submit
    _print_info("Submitting to agent server ...")
    resp = _http_post(f"{base}/run", {"task": task})
    if not resp:
        _print_err("Could not reach agent server.")
        _print_err("  Start it with:  python server.py")
        return

    task_id = resp.get("task_id", f"task_{int(time.time())}")
    _print_ok(f"Accepted -- task_id={task_id}")

    # Blank lines so the first dashboard render has room
    print("\n" * DASH_LINES)

    start        = time.time()
    fix_attempt  = 0
    phase        = "Initialising"
    pct          = 5.0
    last_event   = "Waiting for first server update ..."
    status       = "running"
    last_log_len = 0

    while True:
        time.sleep(POLL_INTERVAL)
        elapsed = time.time() - start

        # Fetch task state
        info = _http_get(f"{base}/task/{task_id}")
        if info is None:
            last_event = "Server unreachable -- retrying ..."
            _render_dashboard(task, task_id, status, phase, pct, elapsed, fix_attempt, last_event)
            continue

        status = info.get("status", "running")

        # Infer phase from logs
        log_data  = _http_get(f"{base}/logs?lines=30") or {}
        log_lines = [l.strip() for l in log_data.get("lines", [])]
        if log_lines:
            phase, pct = _infer_phase(log_lines)
            new_lines  = log_lines[last_log_len:]
            if new_lines:
                last_event   = new_lines[-1][:72]
                last_log_len = len(log_lines)

        # -- SUCCESS -----------------------------------------------------------
        if status == "done":
            pct        = 100.0
            phase      = "Saving Results"
            last_event = "Pipeline complete"
            _render_dashboard(task, task_id, status, phase, pct, elapsed, fix_attempt, last_event)
            result = info.get("result", {})
            _print_ok(f"Score={result.get('score','N/A')}  Passed={result.get('passed','N/A')}")
            _print_ok(f"Project: {result.get('project_dir','N/A')}")
            _memory_record_task(mem, task_id, task, result)
            _memory_save(mem)
            _print_info(f"Memory saved  {MEMORY_PATH}")
            return

        # -- ERROR / AUTO-FIX -------------------------------------------------
        if status == "error":
            error_msg = info.get("error", "unknown error")
            _memory_record_error(mem, task_id, error_msg)
            _memory_save(mem)

            if fix_attempt >= MAX_FIX_RETRIES:
                _render_dashboard(task, task_id, "error", phase, pct, elapsed,
                                  fix_attempt, "Max retries exceeded -- giving up")
                _print_err(f"Task failed after {MAX_FIX_RETRIES} auto-fix attempts.")
                _print_err(f"Error: {error_msg[:120]}")
                return

            fix_attempt += 1
            last_event   = f"Auto-fix attempt {fix_attempt}/{MAX_FIX_RETRIES} ..."
            _render_dashboard(task, task_id, "running", "Bug Fixing", 65.0,
                              elapsed, fix_attempt, last_event)
            _print_warn(f"Error detected -- auto-fix attempt {fix_attempt}/{MAX_FIX_RETRIES}")

            # Re-submit the same task; the server's BugFixer loop handles repair
            new_resp = _http_post(f"{base}/run", {"task": task})
            if new_resp:
                task_id    = new_resp.get("task_id", task_id)
                status     = "running"
                last_event = f"Re-submitted as {task_id}"
                _print_info(f"Re-submitted  new task_id={task_id}")
            else:
                _print_err("Re-submit failed. Server may be down.")
                return
            continue

        # -- STILL RUNNING -----------------------------------------------------
        _render_dashboard(task, task_id, status, phase, pct, elapsed, fix_attempt, last_event)

# -----------------------------------------------------------------------------
# Other commands
# -----------------------------------------------------------------------------

def cmd_status(base: str) -> None:
    _print_header("Agent Status")
    info  = _http_get(f"{base}/status")
    mem   = _memory_load()
    stats = mem.get("stats", {})
    if info:
        print(f"\n  {C_BOLD}Server{C_RESET}")
        print(f"    Active : {C_YELLOW}{info.get('active', 0)}{C_RESET}")
        print(f"    Done   : {C_GREEN}{info.get('done', 0)}{C_RESET}")
        print(f"    Errors : {C_RED}{info.get('errors', 0)}{C_RESET}")
        print(f"    Total  : {info.get('total', 0)}")
    else:
        _print_warn("Server not reachable.")
    print(f"\n  {C_BOLD}Memory  {MEMORY_PATH}{C_RESET}")
    print(f"    Total  : {stats.get('total', 0)}")
    print(f"    Passed : {C_GREEN}{stats.get('passed', 0)}{C_RESET}")
    print(f"    Failed : {C_RED}{stats.get('failed', 0)}{C_RESET}")


def cmd_task(base: str, task_id: str) -> None:
    _print_header(f"Task -- {task_id}")
    info = _http_get(f"{base}/task/{task_id}")
    if not info:
        _print_err(f"Task '{task_id}' not found or server unreachable.")
        return
    sc = _status_colour(info.get("status", ""))
    print(f"  Task ID : {C_YELLOW}{info.get('task_id','?')}{C_RESET}")
    print(f"  Status  : {sc}{info.get('status','?')}{C_RESET}")
    t = info.get("task", "")
    if t:
        print(f"  Task    : {t[:100]}")
    r = info.get("result", {})
    if r:
        print(f"  Score   : {C_GREEN}{r.get('score','N/A')}{C_RESET}")
        print(f"  Passed  : {r.get('passed','N/A')}")
        print(f"  Dir     : {r.get('project_dir','N/A')}")
    if info.get("error"):
        print(f"  Error   : {C_RED}{info['error']}{C_RESET}")


def cmd_logs(base: str, lines: int) -> None:
    _print_header(f"Agent Log (last {lines} lines)")
    data = _http_get(f"{base}/logs?lines={lines}")
    if not data:
        _print_err("Cannot reach server.")
        return
    for line in data.get("lines", []):
        l = line.strip()
        if "ERROR" in l:
            print(f"{C_RED}{l}{C_RESET}")
        elif "WARNING" in l:
            print(f"{C_YELLOW}{l}{C_RESET}")
        else:
            print(l)


def cmd_memory() -> None:
    _print_header(f"Agent Memory -- {MEMORY_PATH}")
    mem   = _memory_load()
    stats = mem.get("stats", {})
    print(f"  Total tasks : {stats.get('total', 0)}")
    print(f"  Passed      : {C_GREEN}{stats.get('passed', 0)}{C_RESET}")
    print(f"  Failed      : {C_RED}{stats.get('failed', 0)}{C_RESET}")
    tasks = mem.get("tasks", [])
    if tasks:
        print(f"\n  {C_BOLD}Last 10 tasks:{C_RESET}")
        for t in tasks[-10:]:
            sc  = C_GREEN if t.get("passed") else C_RED
            sym = "" if t.get("passed") else ""
            ts  = t.get("timestamp", "")[:16]
            print(f"    {sc}{sym}{C_RESET} [{ts}] score={t.get('score','?')}  {t.get('task','')[:60]}")
    errors = mem.get("errors", [])
    if errors:
        print(f"\n  {C_BOLD}Last 5 errors:{C_RESET}")
        for e in errors[-5:]:
            ts = e.get("timestamp","")[:16]
            print(f"    {C_RED}[{ts}]{C_RESET} {e.get('error','')[:80]}")

# -----------------------------------------------------------------------------
# Interactive REPL
# -----------------------------------------------------------------------------

_REPL_HELP = f"""
{C_BOLD}Commands:{C_RESET}
  run   <task>    Submit a task with live dashboard & auto-fix
  status          Server + memory status
  task  <id>      Inspect a task
  logs  [N]       Tail last N log lines  (default 50)
  memory          Memory stats & recent tasks
  help            This message
  exit / quit     Exit
"""


def _repl(base: str) -> None:
    global _dashboard_drawn
    _print_header("RAG Agent  Interactive Shell  (type 'help' for commands)")
    mem   = _memory_load()
    stats = mem.get("stats", {})
    print(f"  Memory: {MEMORY_PATH}  |  Tasks recorded: {stats.get('total', 0)}")
    print(_REPL_HELP)

    while True:
        _dashboard_drawn = False      # reset per REPL iteration
        try:
            raw = input(f"\n{C_BOLD}{C_CYAN}rag> {C_RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not raw:
            continue
        parts   = raw.split(None, 1)
        cmd     = parts[0].lower()
        arg     = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("exit", "quit", "q"):
            print("Bye!")
            break
        elif cmd == "help":
            print(_REPL_HELP)
        elif cmd == "run":
            if not arg:
                _print_warn("Usage: run <task description>")
            else:
                cmd_run(base, arg)
        elif cmd == "status":
            cmd_status(base)
        elif cmd == "task":
            if not arg:
                _print_warn("Usage: task <task_id>")
            else:
                cmd_task(base, arg)
        elif cmd == "logs":
            n = int(arg) if arg.isdigit() else 50
            cmd_logs(base, n)
        elif cmd == "memory":
            cmd_memory()
        else:
            _print_warn(f"Unknown command '{cmd}'. Type 'help'.")

# -----------------------------------------------------------------------------
# Signal handler
# -----------------------------------------------------------------------------

def _sigint_handler(sig, frame):
    print(f"\n{C_YELLOW}Interrupted. Type 'exit' or press Ctrl-C again to force-quit.{C_RESET}")

signal.signal(signal.SIGINT, _sigint_handler)

# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="RAG Agent live CLI -- run without arguments for interactive shell",
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run",    help="Submit a task with live dashboard")
    p_run.add_argument("task")

    sub.add_parser("status",         help="Server + memory status")

    p_task = sub.add_parser("task",  help="Inspect a task")
    p_task.add_argument("task_id")

    p_logs = sub.add_parser("logs",  help="Tail agent log")
    p_logs.add_argument("--lines", type=int, default=50)

    sub.add_parser("memory",         help="Dump memory summary")

    args = parser.parse_args()
    cfg  = _load_config()
    base = _base_url(cfg)

    if args.command is None:
        # No subcommand -> interactive REPL
        if not _wait_for_server(base, retries=3):
            _print_warn("Agent server not detected -- start it with: python server.py")
            _print_warn("Offline mode: status/memory commands still work.")
        _repl(base)
        return

    if args.command == "run":
        if not _wait_for_server(base):
            _print_err("Agent server not reachable. Start it with: python server.py")
            sys.exit(1)
        cmd_run(base, args.task)
    elif args.command == "status":
        cmd_status(base)
    elif args.command == "task":
        cmd_task(base, args.task_id)
    elif args.command == "logs":
        cmd_logs(base, args.lines)
    elif args.command == "memory":
        cmd_memory()


if __name__ == "__main__":
    main()