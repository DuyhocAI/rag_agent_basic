"""
rag_agent/cli.py  —  v5.0
CLI for RAG Agent v5 — dual-layer memory (STM + LTM), full system control.

CHATBOT MODE:
  /cwd [path]      — get or set working directory
  /ls [path]       — list directory
  /read <file>     — read file
  /shell <cmd>     — run shell command
  /reset           — clear session (consolidates STM → LTM first)
  /history         — view active conversation window (STM)
  /execlog         — view execution trace
  /memory          — show STM + LTM snapshot for session
  /memory facts    — list all LTM facts
  /memory recall <query>  — search LTM facts/episodes
  /remember <key> = <value> [| category]  — store a fact to LTM
  /screenshot [path]     — take screenshot
  /mouse move <x> <y>    — move mouse
  /mouse click <x> <y>   — left-click
  /key <keys>            — press key(s) e.g. ctrl+c
  /type <text>           — type text
  /session <id>  — switch session
  /mode          — return to menu
  /exit          — quit

INTERNET COMMANDS:
  /search <query>              — DuckDuckGo web search (no API key)
  /fetch <url>                 — fetch and read any URL
  /json <url>                  — fetch URL as JSON
  /wiki <query>                — Wikipedia summary
  /weather <city>              — current weather
  /download <url> [-> path]    — download file to disk
  /checkurl <url>              — check if URL is reachable
  /ip                          — agent machine public IP
  /dns <hostname>              — DNS lookup

PROJECT MODE:
  Live dashboard: phase, %, self-debate, self-fix thread

Usage:
  python cli.py           — menu
  python cli.py chat      — chatbot mode
  python cli.py run "..."  — one-shot project task
"""

from __future__ import annotations

# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

import argparse
import json
import logging
import os
import signal
import sys
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── UI (rich-based) — import từ UI.py ────────────────────────────────────────
from UI import (
    console,
    print_info        as _ui_print_info,
    print_ok          as _ui_print_ok,
    print_warn        as _ui_print_warn,
    print_err         as _ui_print_err,
    print_rule,
    render_main_menu,
    render_chatbot_header,
    render_chatbot_help,
    render_agent_reply,
    render_dashboard,
    render_memory_snapshot,
    render_facts_table,
    render_recall_results,
    render_history,
    render_exec_log,
    render_task_detail,
    render_logs,
    render_dir_listing,
    render_file_content,
    render_project_header,
    make_spinner,
    MAX_FIX_RETRIES   as _UI_MAX_FIX,
    ChatProgressLive,
    AVAILABLE_MODELS,
    AVAILABLE_SUPERVISOR_MODELS,
)

# render_status may or may not be defined in UI.py — import with fallback
try:
    from UI import render_status
except ImportError:
    def render_status(info: dict, mstats: dict) -> None:
        """Fallback status renderer if UI.py doesn't export render_status."""
        if not info:
            print("  [ERR] Could not reach /status — check api_key or server.")
            return
        w = 60
        print(f"\n{'='*w}")
        print(f"  SERVER STATUS")
        print(f"{'='*w}")
        print(f"  Model    : {info.get('model', '?')}")
        print(f"  Port     : {info.get('port', '?')}")
        print(f"  Sessions : {info.get('sessions', 0)}")
        print(f"  Tasks    : {info.get('tasks', 0)}"
              f"  (running={info.get('running',0)}"
              f"  done={info.get('done',0)}"
              f"  errors={info.get('errors',0)})")
        if mstats:
            ls = mstats.get("ltm_stats", {})
            ts = mstats.get("task_stats", {})
            print(f"\n  LTM      : {ls.get('total_facts',0)} facts"
                  f"  |  {ls.get('total_episodes',0)} episodes")
            print(f"  Tasks DB : {ts.get('total',0)} total"
                  f"  |  done={ts.get('done',0)}"
                  f"  |  errors={ts.get('errors',0)}")
        print(f"{'='*w}\n")

# Giữ nguyên tên hàm cũ để không phải sửa code bên dưới
def _print_info(t: str)   -> None: _ui_print_info(t)
def _print_ok(t: str)     -> None: _ui_print_ok(t)
def _print_warn(t: str)   -> None: print(f"[!] {t}")
def _print_err(t: str)    -> None: _ui_print_err(t)
def _print_header(t: str) -> None: print_rule(t)

# Màu fallback (vẫn dùng ở một vài chỗ inline f-string)
C_GREEN = C_CYAN = C_YELLOW = C_RED = C_BLUE = C_MAGENTA = C_WHITE = ""
C_BOLD  = C_RESET = C_DIM = ""

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(r"D:\rag_agent")
CLI_LOG_PATH = BASE_DIR / "cli.log"
CONFIG_PATH  = Path("config.json")

POLL_INTERVAL   = 2
MAX_FIX_RETRIES = 5
PROGRESS_WIDTH  = 40
DASH_LINES      = 13

BASE_DIR.mkdir(parents=True, exist_ok=True)
CLI_LOG_PATH.write_text(
    f"=== CLI session started {datetime.now().isoformat()} ===\n",
    encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.FileHandler(str(CLI_LOG_PATH), mode="a", encoding="utf-8")],
)
cli_log = logging.getLogger("cli")

# ═══════════════════════════════════════════════════════════════════════════════
# TERMINAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")

# _print_header/info/ok/warn/err đã được định nghĩa trên (delegate sang UI.py)
# Không định nghĩa lại ở đây

def _print_agent(reply: str, exec_count: int = 0, action_count: int = 0,
                 exec_results: list = None) -> None:
    render_agent_reply(reply, exec_count, action_count, exec_results or [])

def _progress_bar(pct: float, width: int = PROGRESS_WIDTH) -> str:
    pct    = max(0.0, min(100.0, pct))
    filled = int(width * pct / 100)
    bar    = "#" * filled + "." * (width - filled)
    return f"{C_CYAN}[{C_GREEN}{bar}{C_CYAN}]{C_RESET} {C_BOLD}{pct:5.1f}%{C_RESET}"

def _sc(s: str) -> str:
    return {"running": C_YELLOW, "done": C_GREEN, "error": C_RED}.get(s, C_WHITE)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG & HTTP
# ═══════════════════════════════════════════════════════════════════════════════

def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        _print_err("config.json not found.")
        sys.exit(1)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

def _save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                           encoding="utf-8")

def _base_url(cfg: dict) -> str:
    host = cfg.get("server", {}).get("host", "127.0.0.1")
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    port = int(cfg.get("server", {}).get("port", 8765))
    return f"http://{host}:{port}"

def _api_key(cfg: dict = None) -> str:
    """Read API key from config.json or RAG_API_KEY env var."""
    if cfg is None:
        try: cfg = _load_config()
        except SystemExit: return ""
    return cfg.get("api_key") or os.environ.get("RAG_API_KEY", "")

def _auth_headers(cfg: dict = None) -> dict:
    key = _api_key(cfg)
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h

def _http_get(url: str, timeout: int = 10) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers=_auth_headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:200]
        cli_log.debug("GET %s → %d: %s", url, exc.code, body)
        if exc.code == 401: _print_err("Authentication failed — check api_key in config.json")
        elif exc.code == 429: _print_err("Rate limit exceeded — slow down requests")
        elif exc.code == 403: _print_err("Access denied (admin role required for this action)")
        return None
    except Exception as exc:
        cli_log.debug("GET %s: %s", url, exc)
        return None

def _http_post(url: str, payload: dict, timeout: int = 60) -> Optional[dict]:
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data, headers=_auth_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:200]
        cli_log.debug("POST %s → %d: %s", url, exc.code, body)
        if exc.code == 401: _print_err("Authentication failed — check api_key in config.json")
        elif exc.code == 429: _print_err("Rate limit exceeded")
        elif exc.code == 403: _print_err("Access denied (admin role required)")
        return None
    except Exception as exc:
        cli_log.debug("POST %s: %s", url, exc)
        return None

def _http_post_raw(url: str, payload: dict, timeout: int = 30) -> Optional[dict]:
    """POST without auth headers — used for /auth/register and /auth/login."""
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:    return json.loads(body)
        except: return {"error": body[:300]}
    except Exception as exc:
        return None

def _wait_for_server(base: str, retries: int = 5) -> bool:
    for i in range(retries):
        if _http_get(f"{base}/", timeout=4):
            return True
        _print_warn(f"Server not ready — retrying ({i+1}/{retries}) ...")
        time.sleep(2)
    return False

# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD (project mode)
# ═══════════════════════════════════════════════════════════════════════════════

_dashboard_drawn = False

def _render_dashboard(task: str, task_id: str, status: str, phase: str,
                      pct: float, elapsed: float, fix_attempt: int,
                      last_event: str, fix_status: str = "") -> None:
    render_dashboard(task, task_id, status, phase, pct,
                     elapsed, fix_attempt, last_event, fix_status)

def _infer_phase(log_lines: list[str]) -> tuple[str, float]:
    text = " ".join(log_lines[-20:]).lower()
    if "complete" in text or "project_meta" in text: return "Complete",         100.0
    if "visuali"  in text:                            return "Visualising",       95.0
    if "model improv" in text:                        return "Model Improving",   88.0
    if "evaluat"  in text or "score"    in text:      return "Evaluating",        80.0
    if "self-fix" in text or "self_fix" in text:      return "Self-Fix Thread",   65.0
    if "bugfix"   in text or "bug_fixer" in text:     return "Bug Fixing",        60.0
    if "test"     in text or "pytest"   in text:      return "Running Tests",     50.0
    if "debate"   in text or "critic"   in text:      return "Self-Debate",       30.0
    if "code_gen" in text or "generating" in text:    return "Code Generation",   20.0
    if "rag"      in text or "retriev"  in text:      return "RAG Retrieval",     10.0
    return "Initialising", 5.0

# ═══════════════════════════════════════════════════════════════════════════════
# MEMORY DISPLAY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _display_memory_snapshot(snap: dict) -> None:
    render_memory_snapshot(snap)

def _display_facts(base: str, category: str = None) -> None:
    url  = f"{base}/memory/facts" + (f"?category={category}" if category else "")
    data = _http_get(url)
    if not data:
        _print_err("Could not reach server.")
        return
    facts = data.get("facts", [])
    _print_header(f"LTM Facts ({len(facts)}){' — ' + category if category else ''}")
    if not facts:
        print(f"  {C_DIM}(none){C_RESET}")
        return
    for f in facts:
        cat = f.get("category","?")
        ts  = f.get("updated_at","")[:16]
        imp = f.get("importance", 0)
        print(f"  [{ts}] {C_YELLOW}{f.get('key','')}{C_RESET}"
              f" = {f.get('value','')[:60]}"
              f"  {C_DIM}cat={cat} imp={imp:.1f}{C_RESET}")

# ═══════════════════════════════════════════════════════════════════════════════
# CHATBOT HELP
# ═══════════════════════════════════════════════════════════════════════════════

_CHAT_HELP = f"""
{C_BOLD}Special commands:{C_RESET}
  /cwd [path]            Get or set working directory
  /ls [path]             List directory
  /read <file>           Read file content
  /shell <cmd>           Run shell command
  /reset                 Clear session (STM consolidated → LTM)
  /history               View active conversation window (STM)
  /execlog               View code execution trace

  {C_BOLD}Memory:{C_RESET}
  /memory                Full STM + LTM snapshot for this session
  /memory facts [cat]    List all LTM facts (optional category filter)
  /memory recall <query> Search LTM facts and episodes
  /remember <k>=<v> [|cat]  Store a fact to LTM

  {C_BOLD}System control:{C_RESET}
  /screenshot [path]     Take a screenshot
  /mouse move <x> <y>    Move mouse to (x, y)
  /mouse click <x> <y>   Left-click at (x, y)
  /key <keys>            Press key(s), e.g. ctrl+s, enter, alt+f4
  /type <text>           Type text at current cursor

  /session <id>          Switch session
  /mode                  Return to main menu
  /help                  Show this help
  /exit                  Quit

{C_DIM}─── Public Server (auth) ──────────────────────────{C_RESET}
  /register <user> <pw>  Dang ky tai khoan, server tu tao ragkey_ luu vao config.json
  /setckey <key>         Luu ckey.vn API key cua ban vao config (dung cho LLM calls)
  /login <user> <pw>     Verify credentials & show active keys
  /whoami                Show current user & role
  /keys                  List your API keys
  /newkey [label] [days] Create new API key (saves to config.json)
  /revokekey <id>        Revoke a key by ID
  /usage                 Request stats (last 7 days)
  /connect <host> <port> [key]  Switch to a different server

{C_DIM}Or type any message — agent will reply and may execute code/actions.{C_RESET}
{C_DIM}Agent can: run Python, read/write/delete files, run shell, control mouse/keyboard.{C_RESET}
"""

# ═══════════════════════════════════════════════════════════════════════════════
# CHATBOT REPL
# ═══════════════════════════════════════════════════════════════════════════════

def _chatbot_repl(base: str, session_id: str = "default") -> str:
    cfg = _load_config()
    cwd_resp    = _http_get(f"{base}/chat/cwd/{session_id}")
    current_cwd = cwd_resp.get("cwd", str(BASE_DIR)) if cwd_resp else str(BASE_DIR)
    render_chatbot_header(model=cfg.get("model","?"), provider=base, title=f"CHATBOT  [{session_id}]  cwd: {current_cwd}")
    render_chatbot_help()

    while True:
        short_cwd = Path(current_cwd).name or current_cwd
        try:
            raw_in = input(f"{C_BOLD}{C_GREEN}you [{short_cwd}]> {C_RESET}")
            raw = (raw_in or "").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "exit"

        if not raw:
            continue
        cli_log.info("[CHAT] session=%s input=%r", session_id, raw[:80])

        # ── Slash commands ─────────────────────────────────────────────────────
        if raw.startswith("/"):
            parts = raw.split(None, 1)
            cmd   = parts[0].lower()
            arg   = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("/exit", "/quit"):
                return "exit"

            elif cmd == "/mode":
                return "menu"

            elif cmd == "/help":
                print(_CHAT_HELP)

            # ── CWD ────────────────────────────────────────────────────────────
            elif cmd == "/cwd":
                if arg:
                    resp = _http_post(f"{base}/chat/cwd",
                                      {"session_id": session_id, "cwd": arg})
                    if resp and not resp.get("error"):
                        current_cwd = resp.get("cwd", arg)
                        _print_ok(f"CWD → {current_cwd}")
                    else:
                        _print_err(resp.get("error","Error") if resp else "Server error")
                else:
                    resp = _http_get(f"{base}/chat/cwd/{session_id}")
                    _print_info(f"CWD: {resp.get('cwd','?') if resp else '?'}")

            # ── FS commands ────────────────────────────────────────────────────
            elif cmd == "/ls":
                path = arg or current_cwd
                resp = _http_get(f"{base}/fs/list?path={urllib.parse.quote(path)}")
                if resp and resp.get("success"):
                    render_dir_listing(resp)
                else:
                    _print_err(resp.get("summary","Error") if resp else "Server error")

            elif cmd == "/read":
                if not arg:
                    _print_warn("Usage: /read <file>")
                else:
                    resp = _http_get(f"{base}/fs/read?path={urllib.parse.quote(arg)}")
                    if resp and resp.get("success"):
                        render_file_content(arg, resp.get("content",""))
                    else:
                        _print_err(resp.get("summary","Error") if resp else "Server error")

            elif cmd == "/shell":
                if not arg:
                    _print_warn("Usage: /shell <command>")
                else:
                    _print_info(f"Running: {arg}")
                    url  = (f"{base}/fs/shell?cmd={urllib.parse.quote(arg)}"
                            f"&cwd={urllib.parse.quote(current_cwd)}")
                    resp = _http_get(url, timeout=30)
                    if resp:
                        if resp.get("success"):
                            _print_ok(resp.get("summary", "Done"))
                        else:
                            _print_err(resp.get("summary", "Error"))
                    else:
                        _print_err("Server error")

            # ── Reset ──────────────────────────────────────────────────────────
            elif cmd == "/reset":
                resp = _http_post(f"{base}/chat/reset", {"session_id": session_id})
                if resp:
                    _print_ok("Session reset (STM consolidated → LTM)")
                else:
                    _print_err("Server error")

            # ── History ────────────────────────────────────────────────────────
            elif cmd == "/history":
                resp = _http_get(f"{base}/chat/history/{session_id}")
                if not resp:
                    _print_warn("Could not fetch history.")
                    continue
                hist = [m for m in resp.get("history",[]) if m.get("role") != "system"]
                render_history(hist, resp.get("working_ctx", {}), resp.get("stm_turns", 0))

            # ── Exec log ──────────────────────────────────────────────────────
            elif cmd == "/execlog":
                resp = _http_get(f"{base}/chat/history/{session_id}")
                if not resp:
                    _print_warn("Could not fetch log.")
                    continue
                render_exec_log(resp.get("exec_log", []))

            # ── Memory commands ────────────────────────────────────────────────
            elif cmd == "/memory":
                sub = arg.lower()
                if not sub:
                    resp = _http_get(f"{base}/chat/memory/{session_id}")
                    if resp:
                        _display_memory_snapshot(resp)
                    else:
                        _print_err("Server error")

                elif sub.startswith("facts"):
                    cat_parts = arg.split(None, 1)
                    cat = cat_parts[1].strip() if len(cat_parts) > 1 else None
                    _display_facts(base, cat)

                elif sub.startswith("recall"):
                    query_parts = arg.split(None, 1)
                    if len(query_parts) < 2:
                        _print_warn("Usage: /memory recall <query>")
                    else:
                        query = query_parts[1].strip()
                        resp  = _http_post(f"{base}/memory/recall",
                                           {"query": query, "limit": 10})
                        if resp:
                            facts = resp.get("facts",[])
                            print(f"\n  {C_BOLD}Recall: '{query}' → {len(facts)} result(s){C_RESET}")
                            for f in facts:
                                print(f"  [{f.get('category','?')}] "
                                      f"{C_YELLOW}{f.get('key','')}{C_RESET}"
                                      f" = {f.get('value','')[:70]}")
                            print()
                        else:
                            _print_err("Server error")
                else:
                    _print_warn("Usage: /memory | /memory facts [cat] | /memory recall <q>")

            elif cmd == "/remember":
                # /remember key = value | category
                if "=" not in arg:
                    _print_warn("Usage: /remember <key> = <value> [| category]")
                else:
                    k, rest = arg.split("=", 1)
                    cat_parts = rest.split("|", 1)
                    value = cat_parts[0].strip()
                    cat   = cat_parts[1].strip() if len(cat_parts) > 1 else "general"
                    resp  = _http_post(f"{base}/memory/fact",
                                       {"key": k.strip(), "value": value,
                                        "category": cat, "session_id": session_id,
                                        "importance": 0.8})
                    if resp and resp.get("stored"):
                        _print_ok(f"Fact stored: {k.strip()} = {value} [{cat}]")
                    else:
                        _print_err("Server error")

            # ── Screenshot ────────────────────────────────────────────────────
            elif cmd == "/screenshot":
                url = f"{base}/system/screenshot"
                if arg:
                    url += f"?path={urllib.parse.quote(arg)}"
                resp = _http_post(url, {})
                if resp:
                    sc = C_GREEN if resp.get("success") else C_RED
                    print(f"  {sc}{resp.get('summary','')}{C_RESET}")
                else:
                    _print_err("Server error (is pyautogui installed?)")

            # ── Mouse ─────────────────────────────────────────────────────────
            elif cmd == "/mouse":
                sub_parts = arg.split(None, 3)
                if not sub_parts:
                    _print_warn("Usage: /mouse move <x> <y> | /mouse click <x> <y>")
                    continue
                sub = sub_parts[0].lower()
                if sub == "move" and len(sub_parts) >= 3:
                    try:
                        x, y = int(sub_parts[1]), int(sub_parts[2])
                        resp = _http_post(f"{base}/system/mouse/move?x={x}&y={y}", {})
                        if resp:
                            print(f"  {C_GREEN}{resp.get('summary','')}{C_RESET}")
                    except ValueError:
                        _print_warn("Usage: /mouse move <x> <y>")
                elif sub == "click" and len(sub_parts) >= 3:
                    try:
                        x, y = int(sub_parts[1]), int(sub_parts[2])
                        btn  = sub_parts[3] if len(sub_parts) > 3 else "left"
                        resp = _http_post(
                            f"{base}/system/mouse/click?x={x}&y={y}&button={btn}", {})
                        if resp:
                            print(f"  {C_GREEN}{resp.get('summary','')}{C_RESET}")
                    except ValueError:
                        _print_warn("Usage: /mouse click <x> <y> [button]")
                else:
                    _print_warn("Usage: /mouse move <x> <y> | /mouse click <x> <y>")

            # ── Keyboard ──────────────────────────────────────────────────────
            elif cmd == "/key":
                if not arg:
                    _print_warn("Usage: /key <keys>  e.g. ctrl+s  enter  alt+f4")
                else:
                    resp = _http_post(f"{base}/system/keyboard/press?keys={urllib.parse.quote(arg)}", {})
                    if resp:
                        sc = C_GREEN if resp.get("success") else C_RED
                        print(f"  {sc}{resp.get('summary','')}{C_RESET}")
                    else:
                        _print_err("Server error")

            elif cmd == "/type":
                if not arg:
                    _print_warn("Usage: /type <text>")
                else:
                    resp = _http_post(f"{base}/system/keyboard/type?text={urllib.parse.quote(arg)}", {})
                    if resp:
                        sc = C_GREEN if resp.get("success") else C_RED
                        print(f"  {sc}{resp.get('summary','')}{C_RESET}")
                    else:
                        _print_err("Server error")

            # ── Session ───────────────────────────────────────────────────────
            elif cmd == "/session":
                if arg:
                    session_id = arg
                    cwd_r = _http_get(f"{base}/chat/cwd/{session_id}")
                    current_cwd = cwd_r.get("cwd", str(BASE_DIR)) if cwd_r else str(BASE_DIR)
                    _print_info(f"Session: {session_id}  CWD: {current_cwd}")
                else:
                    _print_warn("Usage: /session <id>")
            # ── Internet / Web commands ───────────────────────────────────────
            elif cmd == "/search":
                if not arg:
                    _print_warn("Usage: /search <query>")
                else:
                    resp = _http_get(f"{base}/internet/search?query={urllib.parse.quote(arg)}&max_results=8")
                    if resp and resp.get("success"):
                        print()
                        for i, r in enumerate(resp.get("results", []), 1):
                            print(f"  [{i}] {r.get('title','')[:70]}")
                            print(f"       {r.get('url','')[:100]}")
                            if r.get("snippet"):
                                print(f"       {r.get('snippet','')[:150]}")
                            print()
                    else:
                        _print_err(resp.get("summary","Search failed") if resp else "Server not responding")

            elif cmd == "/fetch":
                if not arg:
                    _print_warn("Usage: /fetch <url>")
                else:
                    _print_info(f"Fetching {arg[:80]} ...")
                    resp = _http_get(f"{base}/internet/fetch?url={urllib.parse.quote(arg)}", timeout=30)
                    if resp and resp.get("success"):
                        content_text = resp.get("content","")
                        ctype = resp.get("type","?")
                        print(f"\n  [{ctype.upper()}] {resp.get('chars',0):,} chars from {arg[:70]}")
                        print(f"  {'─'*68}")
                        for line in content_text[:3000].splitlines()[:60]:
                            print(f"  {line[:110]}")
                        if len(content_text) > 3000:
                            print(f"  ... ({len(content_text)-3000:,} more chars)")
                        print()
                    else:
                        _print_err(resp.get("summary","Fetch failed") if resp else "Server not responding")

            elif cmd == "/json":
                if not arg:
                    _print_warn("Usage: /json <url>")
                else:
                    _print_info(f"Fetching JSON from {arg[:60]} ...")
                    resp = _http_get(f"{base}/internet/json?url={urllib.parse.quote(arg)}", timeout=20)
                    if resp and resp.get("success"):
                        summary = resp.get("summary","")
                        print(f"\n{summary[:4000]}\n")
                    else:
                        _print_err(resp.get("summary","JSON fetch failed") if resp else "Server not responding")

            elif cmd == "/wiki":
                if not arg:
                    _print_warn("Usage: /wiki <query>")
                else:
                    _print_info(f"Wikipedia: {arg} ...")
                    resp = _http_get(f"{base}/internet/wikipedia?query={urllib.parse.quote(arg)}&sentences=8", timeout=15)
                    if resp and resp.get("success"):
                        print(f"\n  Wikipedia — {resp.get('title','?')}")
                        print(f"  {'─'*68}")
                        for line in resp.get("summary_text","").splitlines():
                            print(f"  {line}")
                        if resp.get("url"):
                            print(f"\n  URL: {resp['url']}")
                        print()
                    else:
                        _print_err(resp.get("summary","Wikipedia failed") if resp else "Server not responding")

            elif cmd == "/weather":
                if not arg:
                    _print_warn("Usage: /weather <city>")
                else:
                    resp = _http_get(f"{base}/internet/weather?city={urllib.parse.quote(arg)}", timeout=15)
                    if resp and resp.get("success"):
                        print(f"\n{resp.get('summary','')}\n")
                    else:
                        _print_err(resp.get("summary","Weather failed") if resp else "Server not responding")

            elif cmd == "/download":
                if not arg:
                    _print_warn("Usage: /download <url> [-> /save/path]")
                else:
                    parts = arg.split("->", 1)
                    url_part  = parts[0].strip()
                    path_part = parts[1].strip() if len(parts) > 1 else ""
                    if not path_part:
                        import posixpath
                        fname     = posixpath.basename(urllib.parse.urlparse(url_part).path) or "download"
                        path_part = str(BASE_DIR / "downloads" / fname)
                    _print_info(f"Downloading {url_part[:70]} \n  -> {path_part}")
                    resp = _http_post(f"{base}/internet/download",
                                      {"url": url_part, "path": path_part}, timeout=120)
                    if resp and resp.get("success"):
                        _print_ok(resp.get("summary","Downloaded"))
                    else:
                        _print_err(resp.get("summary","Download failed") if resp else "Server not responding")

            elif cmd == "/checkurl":
                if not arg:
                    _print_warn("Usage: /checkurl <url>")
                else:
                    resp = _http_get(f"{base}/internet/check?url={urllib.parse.quote(arg)}", timeout=15)
                    if resp:
                        fn = _print_ok if resp.get("success") else _print_warn
                        fn(resp.get("summary","?"))
                    else:
                        _print_err("Server not responding")

            elif cmd == "/ip":
                resp = _http_get(f"{base}/internet/ip", timeout=15)
                if resp and resp.get("success"):
                    _print_ok(resp.get("summary","?"))
                else:
                    _print_err(resp.get("summary","Failed") if resp else "Server not responding")

            elif cmd == "/dns":
                if not arg:
                    _print_warn("Usage: /dns <hostname>")
                else:
                    resp = _http_get(f"{base}/internet/dns?host={urllib.parse.quote(arg)}", timeout=10)
                    if resp and resp.get("success"):
                        _print_ok(resp.get("summary","?"))
                    else:
                        _print_err(resp.get("summary","DNS failed") if resp else "Server not responding")

            # ── Auth / account commands ───────────────────────────────────────
            elif cmd == "/whoami":
                key = _api_key()
                if not key:
                    _print_warn("No api_key set. Use /register or /connect first.")
                else:
                    resp = _http_get(f"{base}/auth/me")
                    if resp and not resp.get("detail"):
                        _print_ok(f"User : {resp.get('username','?')}  "
                                  f"role={resp.get('role','?')}  plan={resp.get('plan','?')}")
                        _print_info(f"Key  : {key[:16]}...")
                    else:
                        _print_err(f"Auth error: {(resp or {}).get('detail','no response')}")

            elif cmd == "/register":
                # Usage: /register <username> <password> [email]
                # Server tu dong tao ragkey_ va cli.py luu vao config.json
                parts2 = arg.split()
                if len(parts2) < 2:
                    _print_warn("Usage: /register <username> <password> [email]")
                else:
                    cfg2 = _load_config()
                    body = {
                        "username":     parts2[0],
                        "password":     parts2[1],
                        "ckey_api_key": cfg2.get("ckey_api_key", ""),  # gui kem ckey neu da set
                    }
                    if len(parts2) > 2: body["email"] = parts2[2]
                    resp = _http_post_raw(f"{base}/auth/register", body)
                    if resp and resp.get("api_key"):
                        ragkey = resp["api_key"]
                        cfg2["api_key"] = ragkey   # luu ragkey_ de auth voi server
                        _save_config(cfg2)
                        _print_ok(f"Registered as '{parts2[0]}' — ragkey saved to config.json")
                        _print_info(f"ragkey: {ragkey}")
                        if not cfg2.get("ckey_api_key"):
                            _print_warn("ckey.vn API key chua duoc set. Dung /setckey <your_ckey_key> de set.")
                    else:
                        _print_err(f"Registration failed: {(resp or {}).get('detail','no response')}")

            elif cmd == "/setckey":
                # /setckey <ckey_api_key>  — luu ckey.vn API key vao config va cap nhat len server
                if not arg.strip():
                    _print_warn("Usage: /setckey <your_ckey.vn_api_key>")
                else:
                    cfg2 = _load_config()
                    cfg2["ckey_api_key"] = arg.strip()
                    _save_config(cfg2)
                    _print_ok("ckey_api_key saved to config.json")
                    # Cap nhat len server neu da dang nhap
                    if cfg2.get("api_key"):
                        resp = _http_post(f"{base}/auth/ckey", {"ckey_api_key": arg.strip()})
                        if resp and resp.get("ok"):
                            _print_ok("ckey_api_key updated on server")
                        else:
                            _print_warn("Could not update on server — will be sent per-request anyway")

            elif cmd == "/login":
                parts2 = arg.split()
                if len(parts2) < 2:
                    _print_warn("Usage: /login <username> <password>")
                else:
                    resp = _http_post_raw(f"{base}/auth/login",
                                          {"username": parts2[0], "password": parts2[1]})
                    if resp and resp.get("user"):
                        u = resp["user"]
                        _print_ok(f"Logged in: {u.get('username')} ({u.get('role')} / {u.get('plan')})")
                        _print_info(f"Active keys: {resp.get('active_keys',0)} — use /keys or /newkey")
                        # Kiem tra ckey.vn key da set chua
                        cfg2 = _load_config()
                        if not cfg2.get("ckey_api_key"):
                            _print_warn("ckey.vn API key chua duoc set. Dung /setckey <key> de set.")
                        else:
                            _print_ok("ckey_api_key: da set — LLM requests se dung key cua ban.")
                    else:
                        _print_err(f"Login failed: {(resp or {}).get('detail','no response')}")

            elif cmd == "/keys":
                resp = _http_get(f"{base}/auth/keys")
                if resp:
                    for k in resp.get("keys", []):
                        icon = "✔" if k.get("is_active") else "✘"
                        print(f"  [{icon}] id={k['id']}  {k.get('label','?'):15s}"
                              f"  {k.get('key_preview','?')}"
                              f"  created={k.get('created_at','?')[:10]}")
                else:
                    _print_err("Could not fetch keys — are you authenticated?")

            elif cmd == "/newkey":
                parts2 = arg.split()
                body   = {"label": parts2[0] if parts2 else "cli",
                          "expires_days": int(parts2[1]) if len(parts2) > 1 else None}
                resp   = _http_post(f"{base}/auth/keys", body)
                if resp and resp.get("api_key"):
                    new_key = resp["api_key"]
                    cfg2 = _load_config(); cfg2["api_key"] = new_key; _save_config(cfg2)
                    _print_ok(f"New key created (label={resp.get('label','?')}) — saved to config.json")
                    _print_warn("Key shown once only — already saved to config.json")
                else:
                    _print_err("Could not create key.")

            elif cmd == "/revokekey":
                if not arg.isdigit():
                    _print_warn("Usage: /revokekey <key_id>  (see IDs with /keys)")
                else:
                    req2 = urllib.request.Request(
                        f"{base}/auth/keys/{arg}",
                        headers=_auth_headers(), method="DELETE")
                    try:
                        with urllib.request.urlopen(req2, timeout=10) as r:
                            json.loads(r.read())
                        _print_ok(f"Key {arg} revoked.")
                    except Exception as e:
                        _print_err(f"Revoke failed: {e}")

            elif cmd == "/usage":
                resp = _http_get(f"{base}/auth/usage?days=7")
                if resp:
                    _print_info(f"Requests (7d): {resp.get('total_requests',0)}"
                                f"  Errors: {resp.get('errors',0)}")
                    for ep in resp.get("by_endpoint", [])[:8]:
                        print(f"  {ep.get('cnt',0):>6}  {ep.get('endpoint','?')}")
                else:
                    _print_err("Could not fetch usage.")

            elif cmd == "/connect":
                # /connect <host> <port> [api_key]  — switch server on the fly
                parts2 = arg.split()
                if len(parts2) < 2:
                    _print_warn("Usage: /connect <host> <port> [api_key]")
                else:
                    cfg2 = _load_config()
                    cfg2.setdefault("server", {})["host"] = parts2[0]
                    cfg2.setdefault("server", {})["port"] = int(parts2[1])
                    if len(parts2) > 2: cfg2["api_key"] = parts2[2]
                    _save_config(cfg2)
                    base = _base_url(cfg2)
                    _print_ok(f"Switched to {base}")
                    _print_ok("Reachable!") if _wait_for_server(base, retries=2) \
                        else _print_warn("Not reachable yet — check host/port/firewall")

            elif cmd == "/model":
                # /model               — show current + available models
                # /model <model_name>  — switch agent model
                # /model supervisor <model_name> — switch supervisor model
                cfg2 = _load_config()
                if not arg:
                    _print_header("Available Models")
                    current = cfg2.get("model", "?")
                    sup     = cfg2.get("supervisor_model", "?")
                    console.print(f"  [dim]Agent model      :[/dim] [cyan]{current}[/cyan]")
                    console.print(f"  [dim]Supervisor model :[/dim] [cyan]{sup}[/cyan]")
                    console.print()
                    console.print("  [bold]Agent models:[/bold]")
                    for m in AVAILABLE_MODELS:
                        mark = "[green]← current[/green]" if m == current else ""
                        console.print(f"    [yellow]{m}[/yellow]  {mark}")
                    console.print()
                    console.print("  [bold]Supervisor models:[/bold]")
                    for m in AVAILABLE_SUPERVISOR_MODELS:
                        mark = "[green]← current[/green]" if m == sup else ""
                        console.print(f"    [yellow]{m}[/yellow]  {mark}")
                    console.print()
                    console.print("  Usage: /model <name>  or  /model supervisor <name>")
                elif arg.lower().startswith("supervisor "):
                    new_sup = arg.split(None, 1)[1].strip()
                    cfg2["supervisor_model"] = new_sup
                    _save_config(cfg2)
                    _print_ok(f"Supervisor model switched to: {new_sup}  (restart server to apply)")
                else:
                    cfg2["model"] = arg.strip()
                    _save_config(cfg2)
                    _print_ok(f"Agent model switched to: {arg.strip()}  (restart server to apply)")

            else:
                _print_warn(f"Unknown command: {cmd}  (type /help)")
            continue


        # ── Regular message → POST /chat ──────────────────────────────────────
        # Timeout is generous: supervisor loop + multi-action tasks can take
        # several minutes. The server is NOT dead just because it is thinking.
        _CHAT_TIMEOUT = 600   # 10 minutes hard cap per request

        _print_info("Sending to agent ...")
        _t0 = time.time()

        def _tick_progress(progress_live: "ChatProgressLive"):
            """Poll /chat/progress and update the live progress bar while waiting."""
            tick = 0
            while not _stop_tick.is_set():
                if tick % 2 == 0:   # poll every 2 seconds
                    try:
                        prog = _http_get(
                            f"{base}/chat/progress/{session_id}", timeout=2)
                        if prog:
                            progress_live.update(
                                pct    = float(prog.get("pct", 0)),
                                phase  = str(prog.get("phase", "Processing…")),
                                score  = float(prog.get("score", 0.0)),
                                rounds = int(prog.get("rounds", 0)),
                            )
                    except Exception:
                        pass
                tick += 1
                _stop_tick.wait(1)

        _stop_tick = threading.Event()
        with ChatProgressLive(session_id=session_id) as _prog_live:
            _tick_thread = threading.Thread(
                target=_tick_progress, args=(_prog_live,), daemon=True)
            _tick_thread.start()
            try:
                resp = _http_post(
                    f"{base}/chat",
                    {"message": raw, "session_id": session_id,
                     "ckey_api_key": _load_config().get("ckey_api_key", "")},
                    timeout=_CHAT_TIMEOUT,
                )
            finally:
                _stop_tick.set()
                _tick_thread.join(timeout=2)
                # Show 100% briefly before the Live context exits
                _prog_live.update(100.0, "Complete", 1.0, 0)

        if not resp:
            _print_err(
                f"No response after {int(time.time()-_t0)}s — "
                "server may have crashed or the request timed out. "
                "Check server terminal for errors."
            )
            continue

        reply        = resp.get("reply", "(no reply)")
        exec_count   = resp.get("exec_count", 0)
        action_count = resp.get("action_count", 0)
        exec_results = resp.get("exec_results", [])
        stm_turns    = resp.get("stm_turns", "?")

        _print_agent(reply, exec_count, action_count, exec_results)

        if resp.get("cwd"):
            current_cwd = resp["cwd"]

        hist_len = resp.get("history_len", "?")
        _print_info(
            f"STM turns: {stm_turns}  |  Window: {hist_len}  |  "
            f"Exec: {exec_count}  |  Actions: {action_count}"
        )

# ═══════════════════════════════════════════════════════════════════════════════
# STATUS / MEMORY / LOGS / TASK
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_status(base: str) -> None:
    info   = _http_get(f"{base}/status")
    mstats = _http_get(f"{base}/memory/stats")
    render_status(info, mstats)

def cmd_memory(base: str) -> None:
    print_rule("Long-Term Memory (global)")
    _display_facts(base)

def cmd_task(base: str, task_id: str) -> None:
    info = _http_get(f"{base}/task/{task_id}")
    if not info:
        _print_err(f"Task '{task_id}' not found.")
        return
    render_task_detail(info)

def cmd_logs(base: str, lines: int) -> None:
    data = _http_get(f"{base}/logs?lines={lines}")
    if not data:
        _print_err("Cannot connect to server.")
        return
    render_logs(data.get("lines", []))

# ═══════════════════════════════════════════════════════════════════════════════
# PROJECT RUN
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_run(base: str, task: str) -> None:
    global _dashboard_drawn
    _dashboard_drawn = False
    _print_header(f"PROJECT TASK  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _print_info(f"Task: {task}")
    _print_info("Submitting to agent server ...")

    resp = _http_post(f"{base}/run", {"task": task})
    if not resp:
        _print_err("Cannot connect. Run: python server.py")
        return

    task_id = resp.get("task_id", f"task_{int(time.time())}")
    _print_ok(f"Submitted — task_id={task_id}")
    print("\n" * DASH_LINES)

    start = time.time()
    fix_attempt, phase, pct = 0, "Initialising", 5.0
    last_event, status, fix_status = "Waiting ...", "running", ""
    last_log_len = 0

    while True:
        time.sleep(POLL_INTERVAL)
        elapsed = time.time() - start
        info = _http_get(f"{base}/task/{task_id}")
        if info is None:
            last_event = "Server not responding — retrying ..."
            _render_dashboard(task, task_id, status, phase, pct,
                              elapsed, fix_attempt, last_event, fix_status)
            continue

        status     = info.get("status", "running")
        fix_status = info.get("fix_status") or ""
        phase      = info.get("phase", phase)
        pct        = float(info.get("pct", pct))

        log_data  = _http_get(f"{base}/logs?lines=30") or {}
        log_lines = [l.strip() for l in log_data.get("lines", [])]
        if log_lines:
            if not info.get("phase"):
                phase, pct = _infer_phase(log_lines)
            new_lines = log_lines[last_log_len:]
            if new_lines:
                last_event   = new_lines[-1][:68]
                last_log_len = len(log_lines)

        if status == "done":
            pct = 100.0; phase = "Complete"; last_event = "Done!"
            _render_dashboard(task, task_id, status, phase, pct,
                              elapsed, fix_attempt, last_event, fix_status)
            result = info.get("result", {})
            _print_ok(f"Score={result.get('score','N/A')}  Passed={result.get('passed','N/A')}")
            _print_ok(f"Project: {result.get('project_dir','N/A')}")
            if result.get("note"):
                _print_info(f"Note: {result['note']}")
            return

        if status == "error":
            error_msg = info.get("error", "unknown error")
            if fix_status and "fixing" in fix_status:
                last_event = f"Self-Fix: {fix_status}"
                _render_dashboard(task, task_id, "running", "Self-Fix Thread",
                                  65.0, elapsed, fix_attempt, last_event, fix_status)
                continue
            if fix_attempt >= MAX_FIX_RETRIES:
                _render_dashboard(task, task_id, "error", phase, pct, elapsed,
                                  fix_attempt, "Max retries exceeded", fix_status)
                _print_err(f"Task failed after {MAX_FIX_RETRIES} attempts.")
                _print_err(f"Error: {error_msg[:200]}")
                return
            fix_attempt += 1
            last_event   = f"CLI auto-fix attempt {fix_attempt}/{MAX_FIX_RETRIES} ..."
            _render_dashboard(task, task_id, "running", "Bug Fixing", 65.0,
                              elapsed, fix_attempt, last_event, fix_status)
            new_resp = _http_post(f"{base}/run", {"task": task})
            if new_resp:
                task_id = new_resp.get("task_id", task_id)
                status  = "running"; last_log_len = 0
            else:
                _print_err("Re-submit failed.")
                return
            continue

        _render_dashboard(task, task_id, status, phase, pct,
                          elapsed, fix_attempt, last_event, fix_status)

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN MENU
# ═══════════════════════════════════════════════════════════════════════════════

def _project_repl(base: str) -> None:
    print()
    print("=== Project / Agent Mode ===")
    print(f"Base: {base}")
    print("Type 'help' for commands, 'q' to quit.")
    while True:
        try:
            cmd = input("project> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not cmd:
            continue

        c = cmd.lower()
        if c in ("q", "quit", "exit"):
            print("Leaving Project / Agent Mode.")
            break
        if c == "help":
            print("Available commands:")
            print("  help  Show this help message")
            print("  q     Quit Project / Agent Mode")
            continue

        print(f"Unknown command: {cmd}")
        print("Type 'help' for commands.")
def _main_menu(base: str) -> None:
    while True:
        render_main_menu()  # UI.py render đẹp với rich panels
        try:
            choice_in = input(f"{C_BOLD}{C_CYAN}Choose [1/2/3/4/5/q]: {C_RESET}")
            choice = (choice_in or "").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!"); break

        if choice in ("q","quit","exit"):
            print("Bye!"); break
        elif choice == "1":
            result = _chatbot_repl(base)
            if result == "exit": print("Bye!"); break
        elif choice == "2":
            try:
                _project_repl(base)
            except Exception:
                print()
        elif choice == "3":
            cmd_status(base)
            input(f"\n{C_DIM}Press Enter to continue ...{C_RESET}")
        elif choice == "4":
            cmd_memory(base)
            input(f"\n{C_DIM}Press Enter to continue ...{C_RESET}")
        elif choice == "5":
            try:
                n_str_in = input(f"Lines [{C_DIM}50{C_RESET}]: ")
                n_str = (n_str_in or "").strip()
                n = int(n_str) if n_str.isdigit() else 50
            except (EOFError, KeyboardInterrupt):
                n = 50
            cmd_logs(base, n)
            input(f"\n{C_DIM}Press Enter ...{C_RESET}")
        else:
            _print_warn("Invalid choice.")

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL & ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def _sigint(sig, frame):
    print(f"\n{C_YELLOW}Interrupted. Type /exit or Ctrl-C again to quit.{C_RESET}")
signal.signal(signal.SIGINT, _sigint)


def _ensure_auth(base: str) -> None:
    """
    If config.json has no api_key, prompt the user to login or register
    before entering the main menu.  Saves the key to config.json on success.
    Skips if the server is not the public_server (no /auth/register endpoint).
    """
    cfg = _load_config()
    if cfg.get("api_key"):
        # Already have a key — trước tiên check server có auth không
        probe = _http_get(f"{base}/", timeout=4)
        if not probe or "register" not in str(probe):
            # Server local (server.py) — không có auth, bỏ qua
            return
        resp = _http_get(f"{base}/auth/me")
        if resp and not resp.get("detail"):
            _print_ok(f"Authenticated as: {resp.get('username','?')} "
                      f"({resp.get('role','?')} / {resp.get('plan','?')})")
            return
        else:
            _print_warn("Stored api_key is invalid or expired — please log in again.")
            cfg["api_key"] = ""
            _save_config(cfg)

    # Check whether the server has auth at all (public_server vs raw server.py)
    probe = _http_get(f"{base}/")
    if not probe or "register" not in str(probe):
        # Raw server.py — no auth needed
        return

    w = 60
    print(f"\n{'='*w}")
    print(f"  RAG Agent — Authentication Required")
    print(f"  Server: {base}")
    print(f"{'='*w}")
    print(f"  [1] Login      (existing account)")
    print(f"  [2] Register   (new account)")
    print(f"  [3] Skip       (read-only / unauthenticated)")
    print(f"{'='*w}")

    while True:
        try:
            choice_in = input("  Choice [1/2/3]: ")
            choice = (choice_in or "").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return

        if choice == "3":
            _print_warn("Skipping auth — most endpoints will return 401.")
            return

        elif choice == "1":
            try:
                username_in = input("  Username: ")
                username = (username_in or "").strip()
                import getpass
                password = getpass.getpass("  Password: ")
            except (EOFError, KeyboardInterrupt):
                print(); return
            resp = _http_post_raw(f"{base}/auth/login",
                                  {"username": username, "password": password})
            if resp and resp.get("api_key"):
                cfg2 = _load_config()
                cfg2["api_key"] = resp["api_key"]
                _save_config(cfg2)
                u = resp.get("user") or resp
                _print_ok(f"Logged in as: {u.get('username','?')} "
                          f"({u.get('role','?')} / {u.get('plan','?')})")
                _print_ok("api_key saved to config.json")
                return
            else:
                _print_err(f"Login failed: {(resp or {}).get('detail', 'no response')}")
                # Let them retry

        elif choice == "2":
            try:
                username_in = input("  Username (min 3 chars): ")
                username = (username_in or "").strip()
                import getpass
                password = getpass.getpass("  Password (min 8 chars): ")
                email_in = input("  Email: ")
                email = (email_in or "").strip()
            except (EOFError, KeyboardInterrupt):
                print(); return
            cfg2 = _load_config()
            body = {
                "username":     username,
                "password":     password,
                "email":        email,
                "ckey_api_key": cfg2.get("ckey_api_key", ""),
            }
            resp = _http_post_raw(f"{base}/auth/register", body)
            if resp and resp.get("api_key"):
                cfg2["api_key"] = resp["api_key"]
                _save_config(cfg2)
                _print_ok(f"Registered as: {username}")
                _print_ok("api_key saved to config.json")
                if not cfg2.get("ckey_api_key"):
                    _print_warn("Tip: set your ckey.vn API key with /setckey <key> in chat mode.")
                return
            else:
                _print_err(f"Registration failed: {(resp or {}).get('detail', 'no response')}")
        else:
            _print_warn("Please enter 1, 2, or 3.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="RAG Agent v5 CLI — no arguments → main menu")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("chat",   help="Go straight to chatbot mode")
    p_run = sub.add_parser("run",  help="One-shot project task")
    p_run.add_argument("task", nargs="+")
    sub.add_parser("status", help="Server status")
    p_task = sub.add_parser("task", help="View task by ID")
    p_task.add_argument("task_id")
    p_logs = sub.add_parser("logs", help="Server log")
    p_logs.add_argument("--lines", type=int, default=50)
    sub.add_parser("memory", help="Show LTM facts")

    args = parser.parse_args()
    cfg  = _load_config()
    base = _base_url(cfg)
    cli_log.info("CLI v5 started | base=%s | command=%s", base, args.command)

    def _maybe_auth():
        """Chỉ chạy _ensure_auth nếu server là public server (có /auth endpoint).
        Server local (server.py) không có auth — bỏ qua hoàn toàn."""
        probe = _http_get(f"{base}/", timeout=4)
        if probe and "register" in str(probe):
            _ensure_auth(base)

    if args.command is None:
        if not _wait_for_server(base, retries=3):
            _print_warn("Server not started — run: python server.py")
        else:
            _maybe_auth()
        _main_menu(base)
        return

    if args.command == "chat":
        if not _wait_for_server(base):
            _print_err("Server not responding. Run: python server.py"); sys.exit(1)
        _maybe_auth()
        _chatbot_repl(base)
    elif args.command == "run":
        task = " ".join(args.task)
        if not _wait_for_server(base):
            _print_err("Server not responding. Run: python server.py"); sys.exit(1)
        _maybe_auth()
        cmd_run(base, task)
    elif args.command == "status":
        cmd_status(base)
    elif args.command == "task":
        cmd_task(base, args.task_id)
    elif args.command == "logs":
        cmd_logs(base, args.lines)
    elif args.command == "memory":
        cmd_memory(base)

if __name__ == "__main__":
    main()