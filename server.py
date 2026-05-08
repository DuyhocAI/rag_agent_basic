"""
rag_agent/server.py  —  v5.0
Agent with dual-layer memory architecture + full system interaction.

MEMORY ARCHITECTURE:
  SHORT-TERM (in-process RAM):
    - Active conversation turns (sliding window, last N messages)
    - Execution trace (code results, action results this session)
    - Working context: current CWD, open files, task state
    - Evicted to long-term on session end or when window is full

  LONG-TERM (SQLite on disk):
    - Conversation episodes (summarised or full)
    - Task records + outcomes
    - Factual memories: file locations, project structures, user prefs
    - Procedural memories: solutions that worked, patterns the agent learned
    - Semantic search via keyword index (no vector DB required)
    - Auto-consolidated: agent distils STM → LTM every K turns

MODE 1 — CHATBOT  (/chat)
  Real execution: Python, shell, file I/O, mouse/keyboard control.
  Agent retrieves relevant LTM before replying.

MODE 2 — PROJECT AGENT  (/run)
  Generate → Self-Debate → Test → Self-Fix Thread → Evaluate → Improve

SYSTEM ACTIONS:
  READ / WRITE / DELETE / BACKUP / SHELL / LIST_DIR / MOUSE_MOVE /
  MOUSE_CLICK / KEY_PRESS / SCREENSHOT / TYPE_TEXT / MOUSE_SCROLL
"""

from __future__ import annotations

# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
from pydantic import BaseModel

# ═══════════════════════════════════════════════════════════════════════════════
# PATHS & BOOTSTRAP
# ═══════════════════════════════════════════════════════════════════════════════
BASE_DIR    = Path(r"D:\rag_agent")
SERVER_LOG  = BASE_DIR / "server.log"
MEMORY_DB   = BASE_DIR / "agent_memory.db"   # Long-term memory
CONFIG_PATH = Path("config.json")

_HERE   = Path(__file__).resolve().parent
_PARENT = _HERE.parent
for _p in (str(_PARENT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════
BASE_DIR.mkdir(parents=True, exist_ok=True)
SERVER_LOG.write_text(
    f"=== Server session started {datetime.now().isoformat()} ===\n",
    encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(SERVER_LOG), mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("rag_agent.server")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

def _load_config() -> dict:
    for p in (CONFIG_PATH, _HERE / "config.json"):
        if Path(p).exists():
            cfg = json.loads(Path(p).read_text(encoding="utf-8"))
            logger.info("Config: model=%s port=%s",
                        cfg.get("model"), cfg.get("server", {}).get("port", 8765))
            return cfg
    raise FileNotFoundError("config.json not found")

try:
    _CFG = _load_config()
except FileNotFoundError as _e:
    logger.error(str(_e))
    _CFG = {}

_SERVER_HOST          = _CFG.get("server", {}).get("host", "0.0.0.0")
_SERVER_PORT          = int(_CFG.get("server", {}).get("port", 8765))
_MODEL                = _CFG.get("model", "claude-opus-4-6")
_SUPERVISOR_MODEL     = _CFG.get("supervisor_model", "gpt-5.5")
_MAX_TOKENS           = int(_CFG.get("max_tokens", 100000))
_THRESHOLD            = float(_CFG.get("performance_threshold", 0.80))
_MAX_ITER             = int(_CFG.get("max_iterations", 5))
MAX_CODE_RETRIES      = 3
MAX_FIX_ROUNDS        = 5
MAX_DEBATE_ROUNDS     = 3
MAX_SUPERVISOR_ROUNDS = int(_CFG.get("max_supervisor_rounds", 8))

# Short-term memory window: max turns kept in RAM before consolidation
STM_WINDOW        = int(_CFG.get("stm_window", 20))
# Consolidate STM → LTM every N user turns
STM_CONSOLIDATE_EVERY = int(_CFG.get("stm_consolidate_every", 10))

# ═══════════════════════════════════════════════════════════════════════════════
# LLM CLIENT
# ═══════════════════════════════════════════════════════════════════════════════
_CKEY_BASE_URL  = _CFG.get("openai_base_url", "https://ckey.vn/v1")
_DEFAULT_CKEY   = _CFG.get("openai_api_key", os.environ.get("OPENAI_API_KEY", ""))

def _get_client(ckey: str = "") -> OpenAI:
    return OpenAI(api_key=ckey or _DEFAULT_CKEY, base_url=_CKEY_BASE_URL)

_CLIENT = _get_client()  # fallback client

def _llm(system: str, user: str, max_tokens: int = _MAX_TOKENS, ckey: str = "") -> str:
    resp = _get_client(ckey).chat.completions.create(
        model=_MODEL, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system},
                  {"role": "user",   "content": user}],
    )
    return resp.choices[0].message.content or ""

def _llm_messages(messages: list[dict], max_tokens: int = _MAX_TOKENS, ckey: str = "") -> str:
    resp = _get_client(ckey).chat.completions.create(
        model=_MODEL, max_tokens=max_tokens, messages=messages,
    )
    return resp.choices[0].message.content or ""

def _llm_supervisor(system: str, user: str, ckey: str = "", max_tokens: int = 2000) -> str:
    """Call the supervisor model (gpt-5.4). Always uses _SUPERVISOR_MODEL, never _MODEL."""
    resp = _get_client(ckey).chat.completions.create(
        model=_SUPERVISOR_MODEL, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system},
                  {"role": "user",   "content": user}],
    )
    return resp.choices[0].message.content or ""


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:\w+)?\n(.*?)```$", text, re.DOTALL)
    return m.group(1).strip() if m else text

def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

# ═══════════════════════════════════════════════════════════════════════════════
# LONG-TERM MEMORY  (SQLite)
# ═══════════════════════════════════════════════════════════════════════════════

_ltm_lock = threading.Lock()

def _ltm_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(MEMORY_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _ltm_init() -> None:
    """Create schema if not exists."""
    with _ltm_lock:
        conn = _ltm_connect()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS episodes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT    NOT NULL,
            created_at  TEXT    NOT NULL,
            summary     TEXT    NOT NULL,
            full_json   TEXT,
            turn_count  INTEGER DEFAULT 0,
            tags        TEXT    DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS facts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT,
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL,
            category    TEXT    DEFAULT 'general',
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL,
            importance  REAL    DEFAULT 0.5
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     TEXT    UNIQUE,
            session_id  TEXT,
            task        TEXT    NOT NULL,
            status      TEXT    DEFAULT 'pending',
            score       REAL,
            passed      INTEGER DEFAULT 0,
            project_dir TEXT,
            note        TEXT,
            created_at  TEXT    NOT NULL,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS procedures (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger     TEXT    NOT NULL,
            solution    TEXT    NOT NULL,
            success_cnt INTEGER DEFAULT 1,
            fail_cnt    INTEGER DEFAULT 0,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stats (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
        CREATE INDEX IF NOT EXISTS idx_facts_key        ON facts(key);
        CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status);
        """)
        conn.commit()
        conn.close()
    logger.info("[LTM] Database initialised: %s", MEMORY_DB)

_ltm_init()


class LongTermMemory:
    """Thread-safe long-term memory backed by SQLite."""

    # ── Episodes ──────────────────────────────────────────────────────────────

    def store_episode(self, session_id: str, summary: str,
                      full_history: list[dict], tags: list[str] = None) -> int:
        tags_str = ",".join(tags or [])
        full_json = json.dumps(full_history, ensure_ascii=False)
        with _ltm_lock:
            conn = _ltm_connect()
            cur = conn.execute(
                "INSERT INTO episodes (session_id, created_at, summary, full_json, "
                "turn_count, tags) VALUES (?,?,?,?,?,?)",
                (session_id, _now_iso(), summary, full_json,
                 len(full_history), tags_str),
            )
            row_id = cur.lastrowid
            conn.commit(); conn.close()
        logger.info("[LTM] Episode stored: session=%s id=%d turns=%d",
                    session_id, row_id, len(full_history))
        return row_id

    def search_episodes(self, query: str, session_id: str = None,
                        limit: int = 5) -> list[dict]:
        """Keyword search over episode summaries."""
        words = [w.lower() for w in query.split() if len(w) > 2]
        if not words:
            return []
        conditions = " OR ".join(["LOWER(summary) LIKE ?" for _ in words])
        params: list = [f"%{w}%" for w in words]
        sql = f"SELECT id, session_id, created_at, summary, turn_count, tags FROM episodes WHERE ({conditions})"
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with _ltm_lock:
            conn = _ltm_connect()
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            conn.close()
        return rows

    def get_recent_episodes(self, session_id: str, limit: int = 3) -> list[dict]:
        with _ltm_lock:
            conn = _ltm_connect()
            rows = [dict(r) for r in conn.execute(
                "SELECT id, created_at, summary, turn_count FROM episodes "
                "WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit)).fetchall()]
            conn.close()
        return rows

    # ── Facts ─────────────────────────────────────────────────────────────────

    def remember_fact(self, key: str, value: str, category: str = "general",
                      session_id: str = None, importance: float = 0.5) -> None:
        now = _now_iso()
        with _ltm_lock:
            conn = _ltm_connect()
            existing = conn.execute(
                "SELECT id FROM facts WHERE key=? AND (session_id=? OR session_id IS NULL)",
                (key, session_id)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE facts SET value=?, updated_at=?, importance=? WHERE id=?",
                    (value, now, importance, existing["id"]))
            else:
                conn.execute(
                    "INSERT INTO facts (session_id,key,value,category,created_at,updated_at,importance) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (session_id, key, value, category, now, now, importance))
            conn.commit(); conn.close()

    def recall_facts(self, query: str, category: str = None, limit: int = 10) -> list[dict]:
        words = [w.lower() for w in query.split() if len(w) > 2]
        conditions = ["LOWER(key) LIKE ? OR LOWER(value) LIKE ?" for _ in words]
        params: list = []
        for w in words:
            params += [f"%{w}%", f"%{w}%"]
        sql = "SELECT key, value, category, updated_at, importance FROM facts"
        where = []
        if conditions:
            where.append("(" + " OR ".join(conditions) + ")")
        if category:
            where.append("category=?")
            params.append(category)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        with _ltm_lock:
            conn = _ltm_connect()
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            conn.close()
        return rows

    def list_facts(self, category: str = None, limit: int = 50) -> list[dict]:
        with _ltm_lock:
            conn = _ltm_connect()
            if category:
                rows = [dict(r) for r in conn.execute(
                    "SELECT key,value,category,updated_at,importance FROM facts "
                    "WHERE category=? ORDER BY importance DESC LIMIT ?",
                    (category, limit)).fetchall()]
            else:
                rows = [dict(r) for r in conn.execute(
                    "SELECT key,value,category,updated_at,importance FROM facts "
                    "ORDER BY importance DESC LIMIT ?", (limit,)).fetchall()]
            conn.close()
        return rows

    # ── Tasks ─────────────────────────────────────────────────────────────────

    def record_task(self, task_id: str, task: str, session_id: str = None,
                    status: str = "pending") -> None:
        with _ltm_lock:
            conn = _ltm_connect()
            conn.execute(
                "INSERT OR REPLACE INTO tasks "
                "(task_id,session_id,task,status,created_at) VALUES (?,?,?,?,?)",
                (task_id, session_id, task, status, _now_iso()))
            conn.commit(); conn.close()

    def update_task(self, task_id: str, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [task_id]
        with _ltm_lock:
            conn = _ltm_connect()
            conn.execute(f"UPDATE tasks SET {sets} WHERE task_id=?", vals)
            conn.commit(); conn.close()

    def get_task_stats(self) -> dict:
        with _ltm_lock:
            conn = _ltm_connect()
            total   = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            passed  = conn.execute("SELECT COUNT(*) FROM tasks WHERE passed=1").fetchone()[0]
            failed  = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='error'").fetchone()[0]
            conn.close()
        return {"total": total, "passed": passed, "failed": failed}

    def get_recent_tasks(self, limit: int = 10) -> list[dict]:
        with _ltm_lock:
            conn = _ltm_connect()
            rows = [dict(r) for r in conn.execute(
                "SELECT task_id,task,status,score,passed,created_at FROM tasks "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
            conn.close()
        return rows

    # ── Procedures (learned solutions) ────────────────────────────────────────

    def store_procedure(self, trigger: str, solution: str) -> None:
        now = _now_iso()
        with _ltm_lock:
            conn = _ltm_connect()
            existing = conn.execute(
                "SELECT id, success_cnt FROM procedures WHERE trigger=?",
                (trigger,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE procedures SET solution=?, success_cnt=?, updated_at=? WHERE id=?",
                    (solution, existing["success_cnt"] + 1, now, existing["id"]))
            else:
                conn.execute(
                    "INSERT INTO procedures (trigger,solution,created_at,updated_at) VALUES (?,?,?,?)",
                    (trigger, solution, now, now))
            conn.commit(); conn.close()

    def recall_procedures(self, query: str, limit: int = 3) -> list[dict]:
        words = [w.lower() for w in query.split() if len(w) > 2]
        if not words:
            return []
        cond = " OR ".join(["LOWER(trigger) LIKE ?" for _ in words])
        params = [f"%{w}%" for w in words] + [limit]
        with _ltm_lock:
            conn = _ltm_connect()
            rows = [dict(r) for r in conn.execute(
                f"SELECT trigger, solution, success_cnt FROM procedures "
                f"WHERE ({cond}) ORDER BY success_cnt DESC LIMIT ?",
                params).fetchall()]
            conn.close()
        return rows

    # ── Stats ─────────────────────────────────────────────────────────────────

    def inc_stat(self, key: str, by: int = 1) -> None:
        with _ltm_lock:
            conn = _ltm_connect()
            existing = conn.execute("SELECT value FROM stats WHERE key=?", (key,)).fetchone()
            if existing:
                conn.execute("UPDATE stats SET value=? WHERE key=?",
                             (str(int(existing["value"]) + by), key))
            else:
                conn.execute("INSERT INTO stats (key,value) VALUES (?,?)", (key, str(by)))
            conn.commit(); conn.close()

    def get_stats(self) -> dict:
        with _ltm_lock:
            conn = _ltm_connect()
            rows = {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM stats").fetchall()}
            conn.close()
        return rows


# Singleton LTM
_LTM = LongTermMemory()


# ═══════════════════════════════════════════════════════════════════════════════
# SHORT-TERM MEMORY
# ═══════════════════════════════════════════════════════════════════════════════

class ShortTermMemory:
    """
    In-process RAM memory for an active session.

    Structure:
      turns         : list of {role, content} — the active conversation window
      working_ctx   : mutable dict (cwd, open_files, last_action, etc.)
      exec_trace    : recent code / action results this session
      turn_count    : total turns since session start (never resets)
      _pending_ltm  : turns buffered for next LTM consolidation
    """

    def __init__(self, session_id: str):
        self.session_id   = session_id
        self.turns:  list[dict] = []          # active window
        self.working_ctx: dict  = {}          # live context
        self.exec_trace:  list[dict] = []     # execution log
        self.action_log:  list[dict] = []
        self.turn_count:  int   = 0
        self._consolidation_turn = 0          # last turn when we consolidated

    # ── Conversation window ────────────────────────────────────────────────────

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append({"role": role, "content": content})
        if role == "user":
            self.turn_count += 1
            _LTM.inc_stat("chat_turns")

        # Trim window: keep last STM_WINDOW turns
        if len(self.turns) > STM_WINDOW * 2:
            # Keep system turns (none here) + last STM_WINDOW * 2
            self.turns = self.turns[-(STM_WINDOW * 2):]

    def get_messages(self, system_prompt: str) -> list[dict]:
        """Return messages list ready for the LLM."""
        return [{"role": "system", "content": system_prompt}] + self.turns

    def should_consolidate(self) -> bool:
        return (self.turn_count - self._consolidation_turn) >= STM_CONSOLIDATE_EVERY

    # ── Working context ────────────────────────────────────────────────────────

    def set_ctx(self, key: str, value: Any) -> None:
        self.working_ctx[key] = value

    def get_ctx(self, key: str, default: Any = None) -> Any:
        return self.working_ctx.get(key, default)

    def ctx_summary(self) -> str:
        """One-line string injected into every LLM call."""
        cwd = self.working_ctx.get("cwd", "?")
        open_files = self.working_ctx.get("open_files", [])
        last_action = self.working_ctx.get("last_action", "")
        parts = [f"CWD: {cwd}"]
        if open_files:
            parts.append(f"Open: {', '.join(open_files[-3:])}")
        if last_action:
            parts.append(f"Last: {last_action}")
        return " | ".join(parts)

    # ── Execution trace ────────────────────────────────────────────────────────

    def log_exec(self, code: str, result: dict) -> None:
        self.exec_trace.append({
            "code": code[:300], "result": result.get("summary","")[:300],
            "success": result.get("success", False), "time": _now_iso(),
        })
        self.exec_trace = self.exec_trace[-50:]

    def log_action(self, action: dict) -> None:
        self.action_log.append(action)
        self.action_log = self.action_log[-50:]
        self.set_ctx("last_action", f"{action.get('verb','')} {action.get('target','')[:40]}")

    # ── Consolidation into LTM ─────────────────────────────────────────────────

    def consolidate(self, ckey: str = "") -> None:
        """Summarise current turns into LTM and compress the window."""
        if not self.turns:
            return
        logger.info("[STM] Consolidating %d turns for session=%s",
                    len(self.turns), self.session_id)
        turns_text = "\n".join(
            f"[{t['role'].upper()}]: {t['content'][:300]}" for t in self.turns[-20:]
        )
        summary = _llm(
            "You are a memory consolidation assistant. Summarise the following "
            "conversation turns into a concise paragraph capturing the key topics, "
            "decisions, and outcomes. Be factual and brief.",
            f"Session: {self.session_id}\nTurns:\n{turns_text}",
            max_tokens=400,
            ckey=ckey,
        )
        # Extract notable facts for LTM
        facts_raw = _llm(
            "You are a fact extractor. From the conversation below, extract up to 5 "
            "important facts (file paths, user preferences, project names, solutions found). "
            "Respond ONLY with JSON: [{\"key\":\"...\",\"value\":\"...\",\"category\":\"...\"}]. "
            "Categories: file, preference, project, solution, error. Return [] if nothing notable.",
            f"Conversation:\n{turns_text}",
            max_tokens=400,
            ckey=ckey,
        )
        try:
            clean = re.sub(r"```(?:json)?|```", "", facts_raw).strip()
            facts = json.loads(clean)
            for f in facts[:5]:
                _LTM.remember_fact(
                    key=str(f.get("key","")),
                    value=str(f.get("value","")),
                    category=str(f.get("category","general")),
                    session_id=self.session_id,
                    importance=0.7,
                )
        except Exception:
            pass

        _LTM.store_episode(
            session_id=self.session_id,
            summary=summary,
            full_history=self.turns[-20:],
            tags=[self.session_id],
        )
        # Compress window: keep only last 4 turns after consolidation
        self.turns = self.turns[-4:]
        self._consolidation_turn = self.turn_count
        logger.info("[STM] Consolidation done. Window trimmed to %d turns.", len(self.turns))

    def reset(self, ckey: str = "") -> None:
        if self.turns:
            self.consolidate(ckey=ckey)
        self.turns.clear()
        self.exec_trace.clear()
        self.action_log.clear()
        self.working_ctx.clear()
        self.turn_count = 0
        self._consolidation_turn = 0


# ═══════════════════════════════════════════════════════════════════════════════
# FILE BACKUP UTILITY
# ═══════════════════════════════════════════════════════════════════════════════

def _backup(path: Path, silent: bool = False) -> Optional[Path]:
    if not path.exists():
        return None
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:21]
    bak = path.with_suffix(f"{path.suffix}.bak.{ts}")
    try:
        shutil.copy2(path, bak)
        if not silent:
            logger.info("Backed up %s → %s", path.name, bak.name)
        return bak
    except Exception as exc:
        logger.warning("Backup failed %s: %s", path, exc)
        return None

def safe_write(path: Path, content: str) -> None:
    _backup(Path(path), silent=True)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")

def safe_delete(path: Path) -> None:
    _backup(Path(path))
    if Path(path).exists():
        Path(path).unlink()

# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

_DEFAULT_CWD    = str(BASE_DIR)
_session_cwd:   dict[str, str] = {}

def get_cwd(session_id: str = "default") -> str:
    return _session_cwd.get(session_id, _DEFAULT_CWD)

def execute_python(code: str, cwd: str = _DEFAULT_CWD,
                   timeout: int = 120) -> dict:
    logger.info("[EXEC] Running Python (%d chars) cwd=%s", len(code), cwd)
    tmp = Path(cwd) / f"_agent_tmp_{int(time.time()*1000)}.py"
    try:
        tmp.write_text(code, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(tmp)],
            capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        ok  = result.returncode == 0
        logger.info("[EXEC] returncode=%d", result.returncode)
        return {
            "success": ok, "stdout": out, "stderr": err,
            "returncode": result.returncode,
            "summary": f"{'OK' if ok else 'ERR'}.\n" + (f"Output:\n{out}" if ok else f"Error:\n{err}"),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Timeout",
                "returncode": -1, "summary": f"Timeout after {timeout}s"}
    except Exception as exc:
        return {"success": False, "stdout": "", "stderr": str(exc),
                "returncode": -1, "summary": f"System error: {exc}"}
    finally:
        try: tmp.unlink()
        except Exception: pass

def execute_shell(cmd: str, cwd: str = _DEFAULT_CWD,
                  timeout: int = 30) -> dict:
    logger.info("[SHELL] cmd=%r cwd=%s", cmd[:80], cwd)
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
        ok  = result.returncode == 0
        out = result.stdout.strip()
        err = result.stderr.strip()
        return {
            "success": ok, "stdout": out, "stderr": err,
            "returncode": result.returncode,
            "summary": out if ok else f"Shell error:\n{err or out}",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Timeout",
                "returncode": -1, "summary": f"Timeout after {timeout}s"}
    except Exception as exc:
        return {"success": False, "stdout": "", "stderr": str(exc),
                "returncode": -1, "summary": f"Error: {exc}"}

def read_file(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"success": False, "summary": f"File not found: {path}"}
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        logger.info("[READ] %s — %d chars", path, len(content))
        return {"success": True, "content": content,
                "summary": f"Read {p.name} OK ({len(content)} chars):\n{content}"}
    except Exception as exc:
        return {"success": False, "summary": f"Read error: {exc}"}

def list_dir(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"success": False, "summary": f"Directory not found: {path}"}
    try:
        items   = list(p.iterdir())
        folders = sorted([i.name for i in items if i.is_dir()])
        files   = sorted([i.name for i in items if i.is_file()])
        summary = (
            f"Directory: {path}\n"
            f"  Folders ({len(folders)}): {', '.join(folders) or '(none)'}\n"
            f"  Files   ({len(files)}):   {', '.join(files[:30]) or '(none)'}"
            + (" ..." if len(files) > 30 else "")
        )
        return {"success": True, "folders": folders, "files": files, "summary": summary}
    except Exception as exc:
        return {"success": False, "summary": f"Error: {exc}"}

# ── Mouse / Keyboard control ──────────────────────────────────────────────────

def _ensure_pyautogui():
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        raise RuntimeError(
            "pyautogui not installed. Run: pip install pyautogui pillow"
        )

def mouse_move(x: int, y: int, duration: float = 0.3) -> dict:
    try:
        pg = _ensure_pyautogui()
        pg.moveTo(x, y, duration=duration)
        return {"success": True, "summary": f"Mouse moved to ({x}, {y})"}
    except Exception as exc:
        return {"success": False, "summary": f"Mouse move error: {exc}"}

def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
    try:
        pg = _ensure_pyautogui()
        pg.click(x, y, button=button, clicks=clicks)
        return {"success": True, "summary": f"Clicked ({x},{y}) {button}×{clicks}"}
    except Exception as exc:
        return {"success": False, "summary": f"Mouse click error: {exc}"}

def mouse_scroll(x: int, y: int, amount: int) -> dict:
    try:
        pg = _ensure_pyautogui()
        pg.scroll(amount, x=x, y=y)
        return {"success": True, "summary": f"Scrolled {amount} at ({x},{y})"}
    except Exception as exc:
        return {"success": False, "summary": f"Scroll error: {exc}"}

def type_text(text: str, interval: float = 0.05) -> dict:
    try:
        pg = _ensure_pyautogui()
        pg.typewrite(text, interval=interval)
        return {"success": True, "summary": f"Typed {len(text)} chars"}
    except Exception as exc:
        return {"success": False, "summary": f"Type error: {exc}"}

def key_press(keys: str) -> dict:
    """keys: e.g. 'ctrl+c', 'enter', 'alt+f4'"""
    try:
        pg = _ensure_pyautogui()
        if "+" in keys:
            parts = [k.strip() for k in keys.split("+")]
            pg.hotkey(*parts)
        else:
            pg.press(keys)
        return {"success": True, "summary": f"Key pressed: {keys}"}
    except Exception as exc:
        return {"success": False, "summary": f"Key press error: {exc}"}

def take_screenshot(save_path: str = None) -> dict:
    try:
        pg = _ensure_pyautogui()
        img = pg.screenshot()
        if save_path:
            img.save(save_path)
            return {"success": True, "summary": f"Screenshot saved to {save_path}",
                    "path": save_path}
        else:
            tmp = BASE_DIR / f"screenshot_{int(time.time())}.png"
            img.save(str(tmp))
            return {"success": True, "summary": f"Screenshot saved to {tmp}", "path": str(tmp)}
    except Exception as exc:
        return {"success": False, "summary": f"Screenshot error: {exc}"}

# ═══════════════════════════════════════════════════════════════════════════════
# ACTION BLOCK PARSER
# ═══════════════════════════════════════════════════════════════════════════════
# Supported verbs:
#   READ / WRITE / DELETE / BACKUP / SHELL / LIST_DIR
#   MOUSE_MOVE / MOUSE_CLICK / MOUSE_SCROLL / KEY_PRESS / TYPE_TEXT / SCREENSHOT
#   REMEMBER_FACT / RECALL_FACTS / STORE_PROCEDURE
# ═══════════════════════════════════════════════════════════════════════════════

_ACTION_RE   = re.compile(
    r"\[ACTION\]\s+(\w+)\s*(.*?)\[/ACTION\]",
    re.DOTALL | re.IGNORECASE,
)
_CODE_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def web_search(query: str, max_results: int = 5) -> dict:
    """
    Tìm kiếm web bằng DuckDuckGo Instant Answer API (không cần API key).
    Fallback: scrape kết quả HTML từ DuckDuckGo.
    """
    import urllib.request, urllib.parse, json as _json, re as _re

    query_enc = urllib.parse.quote_plus(query)

    # --- Thử DuckDuckGo Instant Answer API ---
    try:
        url = f"https://api.duckduckgo.com/?q={query_enc}&format=json&no_redirect=1&no_html=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read().decode("utf-8"))

        results = []
        # Abstract (Wikipedia-style summary)
        if data.get("AbstractText"):
            results.append(f"[Summary] {data['AbstractText'][:400]}")
        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                url_t = topic.get("FirstURL", "")
                results.append(f"- {topic['Text'][:200]}\n  {url_t}")

        if results:
            return {"success": True,
                    "summary": f"Ket qua tim kiem cho: {query}\n\n" + "\n".join(results)}
    except Exception as e:
        logger.warning("[WEB_SEARCH] DDG API failed: %s", e)

    # --- Fallback: scrape HTML DuckDuckGo ---
    try:
        url = f"https://html.duckduckgo.com/html/?q={query_enc}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")

        # Extract result snippets
        snippets = _re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a>', html, _re.DOTALL)
        titles   = _re.findall(
            r'class="result__a"[^>]*>(.*?)</a>', html, _re.DOTALL)

        lines = []
        for i, (title, snippet) in enumerate(zip(titles, snippets)):
            if i >= max_results:
                break
            clean_title   = _re.sub(r"<[^>]+>", "", title).strip()
            clean_snippet = _re.sub(r"<[^>]+>", "", snippet).strip()
            lines.append(f"{i+1}. {clean_title}\n   {clean_snippet}")

        if lines:
            return {"success": True,
                    "summary": f"Ket qua tim kiem cho: {query}\n\n" + "\n".join(lines)}

        return {"success": False, "summary": f"Khong tim thay ket qua cho: {query}"}
    except Exception as e:
        return {"success": False, "summary": f"Loi web search: {e}"}


def _execute_action(act: dict, cwd: str, stm: "ShortTermMemory") -> str:
    verb    = act["verb"].upper()
    target  = act["target"].strip()
    content = act.get("content", "").strip()

    # ── File I/O ──────────────────────────────────────────────────────────────
    if verb == "READ":
        res = read_file(target)
        if res["success"]:
            stm.set_ctx("open_files",
                        stm.get_ctx("open_files", []) + [target])
        return res["summary"]

    elif verb == "WRITE":
        safe_write(Path(target), content)
        return f"Written: {target}"

    elif verb == "DELETE":
        safe_delete(Path(target))
        return f"Deleted: {target}"

    elif verb == "BACKUP":
        bak = _backup(Path(target))
        return f"Backed up: {bak}" if bak else f"Not found: {target}"

    elif verb == "SHELL":
        cmd = target + (" " + content if content else "")
        return execute_shell(cmd, cwd=cwd)["summary"]

    elif verb == "LIST_DIR":
        return list_dir(target if target else cwd)["summary"]

    # ── Mouse / Keyboard ──────────────────────────────────────────────────────
    elif verb == "MOUSE_MOVE":
        try:
            parts = target.split()
            x, y = int(parts[0]), int(parts[1])
            dur = float(parts[2]) if len(parts) > 2 else 0.3
        except (ValueError, IndexError):
            return f"MOUSE_MOVE needs: x y [duration]. Got: {target}"
        return mouse_move(x, y, dur)["summary"]

    elif verb == "MOUSE_CLICK":
        try:
            parts  = target.split()
            x, y   = int(parts[0]), int(parts[1])
            button = parts[2] if len(parts) > 2 else "left"
            clicks = int(parts[3]) if len(parts) > 3 else 1
        except (ValueError, IndexError):
            return f"MOUSE_CLICK needs: x y [button] [clicks]. Got: {target}"
        return mouse_click(x, y, button, clicks)["summary"]

    elif verb == "MOUSE_SCROLL":
        try:
            parts  = target.split()
            x, y   = int(parts[0]), int(parts[1])
            amount = int(parts[2]) if len(parts) > 2 else 3
        except (ValueError, IndexError):
            return f"MOUSE_SCROLL needs: x y [amount]. Got: {target}"
        return mouse_scroll(x, y, amount)["summary"]

    elif verb == "KEY_PRESS":
        return key_press(target)["summary"]

    elif verb == "TYPE_TEXT":
        return type_text(content or target)["summary"]

    elif verb == "SCREENSHOT":
        return take_screenshot(target if target else None)["summary"]

    # ── Memory ops ────────────────────────────────────────────────────────────
    elif verb == "REMEMBER_FACT":
        # target = key, content = value [| category]
        parts = content.split("|", 1)
        value = parts[0].strip()
        cat   = parts[1].strip() if len(parts) > 1 else "general"
        _LTM.remember_fact(target, value, category=cat,
                           session_id=stm.session_id, importance=0.8)
        return f"Fact stored: {target} = {value}"

    elif verb == "RECALL_FACTS":
        facts = _LTM.recall_facts(target or content, limit=8)
        if not facts:
            return "No relevant facts found in long-term memory."
        return "\n".join(f"[{f['category']}] {f['key']}: {f['value']}"
                         for f in facts)

    elif verb == "STORE_PROCEDURE":
        _LTM.store_procedure(trigger=target, solution=content)
        return f"Procedure stored for: {target}"

    elif verb == "WEB_SEARCH":
        query = (target + " " + content).strip()
        result = web_search(query)
        return result["summary"]

    else:
        return f"Unknown verb: {verb}"


def _run_actions(text: str, cwd: str, stm: "ShortTermMemory") -> list[dict]:
    results = []
    for m in _ACTION_RE.finditer(text):
        verb = m.group(1)
        # Group 2 chứa toàn bộ phần sau verb đến [/ACTION]
        # Dòng đầu = target, các dòng sau = content (nếu có)
        rest    = m.group(2).strip()
        lines   = rest.splitlines()
        target  = lines[0].strip() if lines else ""
        content = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        act = {"verb": verb, "target": target, "content": content}
        result = _execute_action(act, cwd, stm)
        logger.info("[ACTION] %s %s → %s", act["verb"], act["target"][:60], str(result)[:80])
        entry = {**act, "result": result}
        results.append(entry)
        stm.log_action(entry)
    return results

# ═══════════════════════════════════════════════════════════════════════════════
# CHAT SYSTEM PROMPT  (injected with live memory context)
# ═══════════════════════════════════════════════════════════════════════════════

_CHAT_SYSTEM_TEMPLATE = """\
You are RAG-Agent v5, an autonomous AI Agent running directly on the user's computer.

REAL CAPABILITIES (server has granted these):
  File I/O  : READ, WRITE, DELETE, BACKUP, LIST_DIR, SHELL
  Mouse/KB  : MOUSE_MOVE, MOUSE_CLICK, MOUSE_SCROLL, KEY_PRESS, TYPE_TEXT, SCREENSHOT
  Code      : Python code blocks (executed automatically, result fed back)
  Memory    : REMEMBER_FACT, RECALL_FACTS, STORE_PROCEDURE
  Web       : WEB_SEARCH (tìm kiếm thông tin thực tế trên internet)

ACTION SYNTAX:
  [ACTION] VERB target_or_args
  optional_content_block
  [/ACTION]

  Examples:
    [ACTION] READ C:\\path\\to\\file.txt [/ACTION]
    [ACTION] WRITE C:\\path\\out.py
    print("hello")
    [/ACTION]
    [ACTION] WEB_SEARCH tin tức AI mới nhất hôm nay [/ACTION]
    [ACTION] WEB_SEARCH latest AI news May 2026 [/ACTION]
    [ACTION] MOUSE_CLICK 500 300 left 2 [/ACTION]
    [ACTION] KEY_PRESS ctrl+s [/ACTION]
    [ACTION] REMEMBER_FACT project_root C:\\Bao_Duy\\rag_agent | project [/ACTION]
    [ACTION] RECALL_FACTS config file location [/ACTION]
    [ACTION] STORE_PROCEDURE fix_import_error
    Add parent dir to sys.path before importing local modules.
    [/ACTION]

RULES:
  - Always USE actions rather than saying "I cannot access".
  - Khi người dùng hỏi tin tức, thời sự, thông tin hiện tại: LUÔN dùng WEB_SEARCH ngay lập tức.
  - KHÔNG tự đoán hay tự viết code requests/curl để tìm web — dùng [ACTION] WEB_SEARCH.
  - After every code block, wait for execution result before proceeding.
  - All writes/deletes are auto-backed-up.
  - For mouse actions, prefer taking a SCREENSHOT first to confirm position.
  - When you learn something useful (file paths, solutions, user prefs), use
    REMEMBER_FACT to persist it to long-term memory.
  - Use RECALL_FACTS at the start of relevant queries to check if you already know.
  - No emoji in responses.

{memory_context}
"""

def _build_system_prompt(stm: ShortTermMemory, user_query: str) -> str:
    # Retrieve relevant LTM context
    episodes  = _LTM.search_episodes(user_query, limit=5)
    facts     = _LTM.recall_facts(user_query, limit=10)
    procs     = _LTM.recall_procedures(user_query, limit=5)

    # Inject thời gian thực — agent luôn biết đúng ngày giờ mà không cần WEB_SEARCH
    now = datetime.now()
    realtime = (
        f"[THOI GIAN THUC] Hom nay la {now.strftime('%A, %d/%m/%Y')}, "
        f"gio hien tai: {now.strftime('%H:%M:%S')}. "
        f"Day la thong tin chinh xac tu server — KHONG can WEB_SEARCH de biet ngay gio."
    )

    parts = [realtime, f"[WORKING CTX] {stm.ctx_summary()}"]
    if facts:
        parts.append("[LONG-TERM FACTS]\n" +
                     "\n".join(f"  {f['key']}: {f['value']}" for f in facts))
    if procs:
        parts.append("[KNOWN SOLUTIONS]\n" +
                     "\n".join(f"  Trigger: {p['trigger']}\n  Solution: {p['solution'][:200]}"
                               for p in procs))
    if episodes:
        parts.append("[RECENT EPISODE SUMMARIES]\n" +
                     "\n".join(f"  [{e['created_at']}] {e['summary'][:200]}" for e in episodes))

    memory_context = "\n\n".join(parts) if parts else ""
    return _CHAT_SYSTEM_TEMPLATE.format(memory_context=memory_context)


# ═══════════════════════════════════════════════════════════════════════════════
# SUPERVISOR  —  gpt-5.4 watches every agent step and decides when to stop
# ═══════════════════════════════════════════════════════════════════════════════

_SUPERVISOR_SYSTEM = """You are a strict Supervisor AI overseeing an agent called RAG-Agent.

Your job:
1. Read the original user request and the agent's full action/response history so far.
2. Decide whether the task is TRULY COMPLETE — every requirement satisfied, files
   actually written, code actually executed, nothing left undone.
3. If not complete, identify EXACTLY what is still missing and instruct the agent.
4. Keep agents efficient — do not let them loop, repeat actions, or over-explain.

You MUST respond with a JSON object (no markdown fences) in this exact format:
{
  "verdict": "COMPLETE" | "CONTINUE" | "REDIRECT",
  "score": 0.0-1.0,
  "done": true | false,
  "feedback": "<concise instruction to agent, or completion confirmation>",
  "missing": ["<what is still unfinished>"]
}

Verdict meanings:
  COMPLETE  — task fully done (score MUST be 1.0), stop the loop, return answer to user.
  CONTINUE  — agent must keep working; feedback tells it what to do next.
  REDIRECT  — agent went off-track; feedback corrects the direction.

IMPORTANT: "done": true is only valid when score is exactly 1.0. Any score below 1.0
means there is still something missing — set "done": false and "verdict": "CONTINUE".
Be strict: if a file was supposed to be created but no WRITE action was issued,
it is NOT done. If code was supposed to run and no execution result was seen, NOT done.
Score 1.0 ONLY when every stated requirement is verifiably and completely met.
"""


class SupervisorVerdict:
    """Parsed result from one supervisor call."""
    __slots__ = ("verdict", "score", "done", "feedback", "missing", "raw")

    def __init__(self, raw: str):
        self.raw = raw
        try:
            clean = re.sub(r"```(?:json)?|```", "", raw).strip()
            data  = json.loads(clean)
        except Exception:
            data = {"verdict": "CONTINUE", "score": 0.5, "done": False,
                    "feedback": raw[:400], "missing": []}
        self.verdict  = str(data.get("verdict",  "CONTINUE")).upper()
        self.score    = float(data.get("score",   0.5))
        # done is only true when score is exactly 1.0 — partial completion is never "done"
        _data_done    = bool(data.get("done", self.verdict == "COMPLETE"))
        self.done     = _data_done and self.score >= 1.0
        self.feedback = str(data.get("feedback",  ""))
        self.missing  = list(data.get("missing",  []))

    def __repr__(self) -> str:
        return (f"SupervisorVerdict(verdict={self.verdict}, score={self.score:.2f}, "
                f"done={self.done}, missing={self.missing})")


class Supervisor:
    """
    Wraps gpt-5.4 as a supervisor that evaluates agent progress after every
    action round and decides whether to stop or redirect.
    """

    def __init__(self, ckey: str = ""):
        self.ckey    = ckey
        self.history: list[dict] = []

    def reset(self) -> None:
        self.history.clear()

    def record_step(self, step_type: str, content: str) -> None:
        """Log an agent step to the supervisor view. No truncation — agent must receive 100% of all messages."""
        self.history.append({
            "type":    step_type,
            "content": content,
            "ts":      _now_iso(),
        })

    def evaluate(self, user_request: str, agent_last_reply: str) -> "SupervisorVerdict":
        """Ask the supervisor to evaluate the current state. Full content — no truncation."""
        steps_text = "\n".join(
            f"[{s['type']}] {s['content']}"
            for s in self.history[-40:]   # expanded from 20 to 40 for full context
        )
        user_prompt = (
            f"=== ORIGINAL USER REQUEST ===\n{user_request}\n\n"
            f"=== AGENT HISTORY (last {len(self.history[-40:])} steps) ===\n{steps_text}\n\n"
            f"=== AGENT LATEST REPLY ===\n{agent_last_reply}"  # no truncation
        )
        raw     = _llm_supervisor(_SUPERVISOR_SYSTEM, user_prompt, ckey=self.ckey,
                                  max_tokens=4000)   # increased from 2000
        verdict = SupervisorVerdict(raw)
        logger.info("[SUPERVISOR] %s score=%.2f missing=%s",
                    verdict.verdict, verdict.score, verdict.missing)
        return verdict

    def debate_with_agent(self, user_request: str, agent_reply: str,
                          verdict: "SupervisorVerdict") -> str:
        """Generate structured feedback to inject back into the agent context."""
        parts = [f"[SUPERVISOR — {verdict.verdict}] score={verdict.score:.2f}"]
        if verdict.feedback:
            parts.append(f"Feedback: {verdict.feedback}")
        if verdict.missing:
            parts.append("Still missing:")
            for m in verdict.missing:
                parts.append(f"  - {m}")
        parts.append("Fix the above, then reply again. Do NOT repeat actions already done.")
        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# CHAT ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class ChatEngine:
    """
    Multi-turn chatbot with dual-layer memory.
    STM handles the active window; LTM stores episodes, facts, and procedures.
    """

    def __init__(self, session_id: str = "default", ckey: str = ""):
        self.session_id = session_id
        self.ckey       = ckey
        self.stm        = ShortTermMemory(session_id)
        self.supervisor = Supervisor(ckey=ckey)   # gpt-5.4 supervisor
        _LTM.inc_stat("sessions")

    @property
    def history(self) -> list[dict]:
        return self.stm.turns

    @property
    def exec_log(self) -> list[dict]:
        return self.stm.exec_trace

    @property
    def action_log(self) -> list[dict]:
        return self.stm.action_log

    def _cwd(self) -> str:
        return get_cwd(self.session_id)

    def chat(self, user_msg: str) -> dict:
        cwd = self._cwd()
        self.stm.set_ctx("cwd", cwd)

        # Supervisor resets its step log for each new user request
        self.supervisor.reset()
        self.supervisor.record_step("USER_REQUEST", user_msg)
        # Reset activity log and progress for this session
        _activity[self.session_id] = [f"[THINKING] Processing request..."]
        _chat_progress[self.session_id] = {"pct": 5, "phase": "Thinking", "score": 0.0, "rounds": 0}

        # Rebuild system prompt with live memory context
        system_prompt = _build_system_prompt(self.stm, user_msg)
        contextual_msg = f"[CWD: {cwd}]\n{user_msg}"
        self.stm.add_turn("user", contextual_msg)

        messages = self.stm.get_messages(system_prompt)
        response = _llm_messages(messages, ckey=self.ckey)
        self.stm.add_turn("assistant", response)
        self.supervisor.record_step("AGENT_REPLY", response)

        all_exec_results:   list[dict] = []
        all_action_results: list[dict] = []
        current_response = response
        current_retry    = 0

        # ── Code execution loop ────────────────────────────────────────────────
        while "```python" in current_response and current_retry < MAX_CODE_RETRIES:
            code_blocks = _CODE_BLOCK_RE.findall(current_response)
            if not code_blocks:
                break
            for code in code_blocks:
                exec_result = execute_python(code.strip(), cwd=cwd)
                all_exec_results.append(exec_result)
                self.stm.log_exec(code, exec_result)
                self.supervisor.record_step("CODE_EXEC", exec_result["summary"])
                logger.info("[CHAT EXEC] session=%s success=%s",
                            self.session_id, exec_result["success"])
                self.stm.add_turn("user", f"Execution result:\n{exec_result['summary']}")

            messages  = self.stm.get_messages(system_prompt)
            follow_up = _llm_messages(messages, ckey=self.ckey)
            self.stm.add_turn("assistant", follow_up)
            self.supervisor.record_step("AGENT_REPLY", follow_up)
            current_response = follow_up

            if all(r["success"] for r in all_exec_results[-len(code_blocks):]):
                if "```python" not in follow_up:
                    break
            current_retry += 1

        # ── SUPERVISOR-GATED ACTION LOOP ──────────────────────────────────────
        # Loop chạy vô hạn cho đến khi supervisor xác nhận COMPLETE (score=1.0)
        # hoặc user ấn Ctrl+C. Không có time limit hay round limit.
        supervisor_round    = 0
        consecutive_no_action = 0  # đếm vòng liên tiếp không có action

        while True:

            # Run any [ACTION] blocks in the current response
            action_results = _run_actions(current_response, cwd, self.stm)
            if action_results:
                consecutive_no_action = 0
                all_action_results.extend(action_results)
                action_summary = "\n".join(
                    f"[{a['verb']} {a['target'][:80]}]: {str(a['result'])[:400]}"
                    for a in action_results
                )
                self.supervisor.record_step("ACTION_BATCH", action_summary)
                self.stm.add_turn("user", f"Action results:\n{action_summary}")
                _activity.setdefault(self.session_id, []).append(
                    f"[ACTIONS] " + " | ".join(
                        f"{a['verb']} {a['target'][:40]}" for a in action_results))

                # Agent replies after seeing action results
                messages       = self.stm.get_messages(system_prompt)
                next_response  = _llm_messages(messages, ckey=self.ckey)
                self.stm.add_turn("assistant", next_response)
                self.supervisor.record_step("AGENT_REPLY", next_response)
                current_response = next_response

            # ── Supervisor evaluates the current state ────────────────────────
            verdict = self.supervisor.evaluate(user_msg, current_response)
            logger.info("[SUPERVISOR] round=%d verdict=%s score=%.2f",
                        supervisor_round, verdict.verdict, verdict.score)
            _activity.setdefault(self.session_id, []).append(
                f"[SUPERVISOR] round={supervisor_round} {verdict.verdict} score={verdict.score:.2f}")
            # Update live chat progress for the UI progress bar
            _chat_progress[self.session_id] = {
                "pct":    round(verdict.score * 100, 1),
                "phase":  f"Supervisor round {supervisor_round + 1} — {verdict.verdict}",
                "score":  verdict.score,
                "rounds": supervisor_round + 1,
            }

            # Chỉ dừng khi supervisor xác nhận COMPLETE với score=1.0
            if verdict.done:
                logger.info("[SUPERVISOR] Task COMPLETE after %d rounds.", supervisor_round + 1)
                break

            # ── No-action: nudge agent nhưng KHÔNG bao giờ stop ──────────────
            if not action_results:
                consecutive_no_action += 1
                nudge_strength = "strong" if consecutive_no_action >= 3 else "normal"
                if nudge_strength == "strong":
                    nudge = (
                        f"[SUPERVISOR — URGENT] Đây là lần thứ {consecutive_no_action} bạn không dùng action. "
                        "Task CHƯA hoàn thành. Bạn BẮT BUỘC phải dùng [ACTION] ... [/ACTION] ngay bây giờ. "
                        "Ví dụ: [ACTION] LIST_DIR D:\\ [/ACTION] để liệt kê thư mục D:\\. "
                        "Chỉ khi có action result thực sự thì supervisor mới có thể xác nhận hoàn thành."
                    )
                else:
                    nudge = (
                        "[SUPERVISOR] Bạn chưa dùng action nào. Task CHƯA xong. "
                        "Hãy dùng [ACTION] ... [/ACTION] ngay để thực hiện yêu cầu của user. "
                        "Không giải thích — hành động ngay."
                    )
                self.stm.add_turn("user", nudge)
                messages       = self.stm.get_messages(system_prompt)
                next_response  = _llm_messages(messages, ckey=self.ckey)
                self.stm.add_turn("assistant", next_response)
                self.supervisor.record_step("AGENT_NUDGED", next_response)
                current_response = next_response
                supervisor_round += 1
                continue

            # ── Inject supervisor feedback và để agent tiếp tục ───────────────
            feedback_msg = self.supervisor.debate_with_agent(
                user_msg, current_response, verdict)
            self.stm.add_turn("user", feedback_msg)
            logger.info("[SUPERVISOR] Injecting feedback (round %d): %s",
                        supervisor_round, feedback_msg[:120])

            messages       = self.stm.get_messages(system_prompt)
            next_response  = _llm_messages(messages, ckey=self.ckey)
            self.stm.add_turn("assistant", next_response)
            self.supervisor.record_step("AGENT_AFTER_FEEDBACK", next_response)
            current_response = next_response
            supervisor_round += 1

        # ── Auto-consolidate STM → LTM if due ─────────────────────────────────
        if self.stm.should_consolidate():
            threading.Thread(
                target=self.stm.consolidate,
                kwargs={"ckey": self.ckey},
                daemon=True,
            ).start()

        _LTM.inc_stat("chat_turns")
        _chat_progress[self.session_id] = {"pct": 100, "phase": "Complete", "score": 1.0, "rounds": supervisor_round}
        return {
            "reply":            current_response,
            "exec_results":     all_exec_results,
            "action_results":   all_action_results,
            "history_len":      len(self.stm.turns),
            "cwd":              cwd,
            "stm_turns":        self.stm.turn_count,
            "supervisor_rounds": supervisor_round,
        }

    def reset(self) -> None:
        self.stm.reset(ckey=self.ckey)

    def memory_snapshot(self) -> dict:
        """Return a snapshot of both STM and LTM for this session."""
        return {
            "session_id":    self.session_id,
            "stm": {
                "active_turns":   len(self.stm.turns),
                "total_turns":    self.stm.turn_count,
                "working_ctx":    self.stm.working_ctx,
                "exec_trace_len": len(self.stm.exec_trace),
            },
            "ltm": {
                "recent_episodes": _LTM.get_recent_episodes(self.session_id, limit=3),
                "recent_facts":    _LTM.list_facts(limit=10),
                "stats":           _LTM.get_stats(),
                "task_stats":      _LTM.get_task_stats(),
            },
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MODE 2 — SELF-DEBATE
# ═══════════════════════════════════════════════════════════════════════════════

_PROPOSER_SYS = ("You are a Proposer agent. Given a coding task and optional "
                 "critic feedback, produce the best Python implementation. "
                 "Output ONLY raw Python code, no markdown fences.")
_CRITIC_SYS   = ("You are a Critic agent. Review Python code for bugs, edge cases, "
                 "performance issues. Be specific. If code is perfect, say only: LGTM")

def _self_debate(task: str, code: str, rounds: int = MAX_DEBATE_ROUNDS) -> str:
    logger.info("[DEBATE] Starting %d rounds", rounds)
    for r in range(1, rounds + 1):
        critique = _llm(_CRITIC_SYS,
                        f"Task:\n{task}\n\nCode:\n```python\n{code}\n```",
                        max_tokens=9000)
        logger.info("[DEBATE] Round %d: %s", r, critique[:120])
        if critique.strip().upper().startswith("LGTM"):
            break
        revised = _llm(_PROPOSER_SYS,
                       f"Task:\n{task}\n\nCode:\n```python\n{code}\n```\n\nCritic:\n{critique}")
        code = _strip_fences(revised) or code
    return code

# ═══════════════════════════════════════════════════════════════════════════════
# MODE 2 — SELF-FIX THREAD
# ═══════════════════════════════════════════════════════════════════════════════

_FIXER_SYS = ("You are an expert Python debugger. Given the task, failing code, "
              "and error output, produce a CORRECTED version. Output ONLY raw Python code.")

def _self_fix_thread(task: str, code: str, error: str,
                     project_dir: Path, record: dict,
                     max_rounds: int = MAX_FIX_ROUNDS) -> None:
    logger.info("[SELF-FIX] Started for %s", project_dir.name)
    current_code, current_error = code, error

    for attempt in range(1, max_rounds + 1):
        record.update({"fix_attempt": attempt, "fix_status": f"fixing_{attempt}",
                       "phase": f"Self-Fix (attempt {attempt}/{max_rounds})"})
        fixed = _strip_fences(_llm(_FIXER_SYS,
            f"Task:\n{task}\n\nFailing code:\n```python\n{current_code}\n```\n\nError:\n{current_error[-3000:]}"))
        current_code = fixed or current_code
        safe_write(project_dir / "main.py", current_code)
        res = execute_python(current_code, cwd=str(project_dir))
        current_error = res["summary"]
        if res["success"]:
            logger.info("[SELF-FIX] Fixed on attempt %d", attempt)
            # Store the working solution as a procedure in LTM
            _LTM.store_procedure(trigger=task[:200], solution=current_code[:1000])
            record.update({
                "fix_status": "fixed", "status": "done",
                "phase": "Complete (Self-Fix)", "pct": 100,
                "result": {"score": 0.75, "passed": True,
                           "project_dir": str(project_dir),
                           "note": f"Fixed by self-fix thread attempt {attempt}"},
                "finished_at": time.time(),
            })
            return

    record.update({
        "fix_status": "exhausted", "status": "error", "phase": "Failed",
        "error": f"Self-fix gave up after {max_rounds} attempts.\n{current_error[-400:]}",
        "finished_at": time.time(),
    })

# ═══════════════════════════════════════════════════════════════════════════════
# MODE 2 — FULL PROJECT PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# MODE 2 — FULL PROJECT PIPELINE  (self-contained, no external RAGAgent import)
# ═══════════════════════════════════════════════════════════════════════════════

_CODEGEN_SYS = (
    "You are an expert Python developer. Given a task description and optional "
    "context, produce a complete, runnable Python solution. "
    "Output ONLY raw Python code — no markdown fences, no explanation."
)

_EVALUATOR_SYS = (
    "You are a code quality evaluator. Given a task and its Python implementation, "
    "score it 0.0-1.0 on: correctness, completeness, readability, edge-case handling. "
    "Respond ONLY with JSON: "
    '{"score": 0.85, "passed": true, "issues": [".."], "strengths": [".."]}'
)

_IMPROVER_SYS = (
    "You are a Python code improver. Given a task, code, and evaluation metrics, "
    "produce an improved version that addresses the listed issues. "
    "Output ONLY raw Python code — no fences, no explanation."
)

_TEST_SYS = (
    "You are a Python test writer. Given a task and its implementation, "
    "write a pytest test file that thoroughly tests the solution. "
    "Output ONLY raw Python code — no markdown fences."
)


def _project_dir(task_id: str) -> Path:
    d = BASE_DIR / "projects" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_tests(project_dir: Path) -> dict:
    """Run pytest in the project directory. Returns {passed, output}."""
    test_file = project_dir / "test_main.py"
    if not test_file.exists():
        # No test file — just try executing main.py
        res = execute_python(
            (project_dir / "main.py").read_text(encoding="utf-8", errors="replace"),
            cwd=str(project_dir),
        )
        return {"passed": res["success"], "output": res["summary"]}
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short"],
            capture_output=True, text=True, timeout=120, cwd=str(project_dir),
        )
        passed = result.returncode == 0
        output = (result.stdout + result.stderr).strip()
        return {"passed": passed, "output": output[:3000]}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "pytest timed out after 120s"}
    except Exception as exc:
        return {"passed": False, "output": str(exc)}


def _evaluate_code(task: str, code: str, test_results: dict, ckey: str = "") -> dict:
    """Ask LLM to score the code. Returns metrics dict."""
    prompt = (
        f"Task:\n{task}\n\n"
        f"Code:\n```python\n{code[:3000]}\n```\n\n"
        f"Test result: {'PASSED' if test_results.get('passed') else 'FAILED'}\n"
        f"Test output:\n{test_results.get('output','')[:500]}"
    )
    raw = _llm(_EVALUATOR_SYS, prompt, max_tokens=600, ckey=ckey)
    try:
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        metrics = json.loads(clean)
    except Exception:
        metrics = {"score": 0.5 if test_results.get("passed") else 0.2,
                   "passed": test_results.get("passed", False),
                   "issues": [], "strengths": []}
    return metrics


def _run_project_task(task_id: str, task: str, record: dict, ckey: str = "") -> None:
    logger.info("[PROJECT] %s — %r", task_id, task[:80])
    _LTM.record_task(task_id, task, status="running")
    project_dir = _project_dir(task_id)

    try:
        # ── 1. Check LTM for known solutions ─────────────────────────────────
        record.update({"phase": "LTM Lookup", "pct": 5})
        known    = _LTM.recall_procedures(task, limit=1)
        context  = f"Known solution hint:\n{known[0]['solution']}\n\n" if known else ""

        # ── 2. Code generation ────────────────────────────────────────────────
        record.update({"phase": "Code Generation", "pct": 15})
        raw_code = _llm(_CODEGEN_SYS,
                        f"Task:\n{task}\n\n{context}",
                        max_tokens=_MAX_TOKENS, ckey=ckey)
        code = _strip_fences(raw_code) or raw_code
        safe_write(project_dir / "main.py", code)
        logger.info("[PROJECT] Code generated (%d chars)", len(code))

        # ── 3. Self-Debate (Proposer ↔ Critic) ───────────────────────────────
        record.update({"phase": "Self-Debate", "pct": 30})
        code = _self_debate(task, code, rounds=MAX_DEBATE_ROUNDS)
        safe_write(project_dir / "main.py", code)

        # ── 4. Generate tests ─────────────────────────────────────────────────
        record.update({"phase": "Generating Tests", "pct": 40})
        test_code = _llm(_TEST_SYS,
                         f"Task:\n{task}\n\nCode:\n```python\n{code[:3000]}\n```",
                         max_tokens=4000, ckey=ckey)
        safe_write(project_dir / "test_main.py", _strip_fences(test_code) or test_code)

        # ── 5. Test → fix loop ────────────────────────────────────────────────
        test_results: dict = {"passed": False, "output": ""}
        for i in range(1, _MAX_ITER + 1):
            record.update({"phase": f"Running Tests (iter {i})", "pct": 45 + i * 4})
            test_results = _run_tests(project_dir)
            logger.info("[PROJECT] iter=%d passed=%s", i, test_results["passed"])

            if test_results["passed"]:
                break

            if i == _MAX_ITER:
                # Hand off to self-fix thread for deep repair
                record.update({"phase": "Self-Fix Thread", "pct": 60})
                t = threading.Thread(
                    target=_self_fix_thread,
                    args=(task, code, test_results["output"], project_dir, record),
                    kwargs={"max_rounds": MAX_FIX_ROUNDS},
                    daemon=True,
                )
                t.start(); t.join(timeout=600)
                return

            # Bug-fix and retry
            record.update({"phase": f"Bug Fixing (iter {i})", "pct": 50 + i * 3})
            fixed = _strip_fences(_llm(
                _FIXER_SYS,
                f"Task:\n{task}\n\nFailing code:\n```python\n{code}\n```\n\nError:\n{test_results['output'][-2000:]}",
                max_tokens=_MAX_TOKENS, ckey=ckey,
            ))
            code = fixed or code
            safe_write(project_dir / "main.py", code)

        # ── 6. Supervisor evaluates the final output ──────────────────────────
        record.update({"phase": "Supervisor Evaluation", "pct": 75})
        sup = Supervisor(ckey=ckey)
        sup.record_step("USER_REQUEST", task)
        sup.record_step("CODE", code[:800])
        sup.record_step("TEST_RESULT",
                        "PASSED" if test_results["passed"] else test_results["output"][:400])
        sup_verdict = sup.evaluate(task, code[:800])
        logger.info("[PROJECT] Supervisor: %s score=%.2f", sup_verdict.verdict, sup_verdict.score)

        # ── 7. LLM metric evaluation ──────────────────────────────────────────
        metrics = _evaluate_code(task, code, test_results, ckey=ckey)
        # Blend supervisor score with LLM evaluator score
        metrics["score"] = round(
            0.5 * metrics.get("score", 0.5) + 0.5 * sup_verdict.score, 3)
        metrics["supervisor_verdict"] = sup_verdict.verdict
        metrics["supervisor_missing"] = sup_verdict.missing
        safe_write(project_dir / "metrics.json", json.dumps(metrics, indent=2))
        score = metrics["score"]

        # ── 8. Improve loop if below threshold ────────────────────────────────
        for i in range(1, _MAX_ITER + 1):
            if score >= _THRESHOLD:
                break
            record.update({"phase": f"Improving (iter {i})", "pct": 78 + i * 2})
            improved = _strip_fences(_llm(
                _IMPROVER_SYS,
                f"Task:\n{task}\n\nCode:\n```python\n{code[:3000]}\n```\n\n"
                f"Issues:\n" + "\n".join(f"- {x}" for x in metrics.get("issues", [])),
                max_tokens=_MAX_TOKENS, ckey=ckey,
            ))
            code  = improved or code
            code  = _self_debate(task, code, rounds=1)
            safe_write(project_dir / "main.py", code)
            test_results = _run_tests(project_dir)
            metrics      = _evaluate_code(task, code, test_results, ckey=ckey)
            score        = metrics.get("score", score)
            safe_write(project_dir / "metrics.json", json.dumps(metrics, indent=2))

        # ── 9. Store to LTM if good enough ───────────────────────────────────
        if test_results.get("passed"):
            _LTM.store_procedure(trigger=task[:200], solution=code[:1000])

        meta = {
            "task_id": task_id, "task": task, "model": _MODEL, "score": score,
            "passed": test_results.get("passed", False),
            "project_dir": str(project_dir),
            "files": [str(project_dir / "main.py"), str(project_dir / "test_main.py")],
            "supervisor_verdict": sup_verdict.verdict,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        safe_write(project_dir / "project_meta.json", json.dumps(meta, indent=2))
        record.update({"status": "done", "result": meta, "phase": "Complete",
                       "pct": 100, "finished_at": time.time()})
        _LTM.update_task(task_id, status="done", score=score,
                         passed=int(bool(test_results.get("passed"))),
                         project_dir=str(project_dir),
                         finished_at=_now_iso())
        logger.info("[PROJECT] DONE %s score=%.3f supervisor=%s",
                    task_id, score, sup_verdict.verdict)

    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("[PROJECT] FAILED %s: %s", task_id, exc)
        _LTM.update_task(task_id, status="error", finished_at=_now_iso())
        main_py = project_dir / "main.py"
        existing = main_py.read_text(encoding="utf-8") if main_py.exists() else ""
        record.update({"phase": "Self-Fix (pipeline error)", "pct": 40})
        t = threading.Thread(
            target=_self_fix_thread,
            args=(task, existing, tb, project_dir, record),
            daemon=True,
        )
        t.start(); t.join(timeout=300)


# ═══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY STORES
# ═══════════════════════════════════════════════════════════════════════════════
_tasks:    dict[str, dict[str, Any]] = {}
_sessions: dict[str, ChatEngine]     = {}
# Live activity log per session — written by chat() for CLI to poll
_activity: dict[str, list[str]]      = {}
# Live progress for chat mode — used by UI progress bar
_chat_progress: dict[str, dict[str, Any]] = {}
_executor  = ThreadPoolExecutor(max_workers=4)

# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI
# ═══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("RAG Agent v5 — %s:%d — model=%s", _SERVER_HOST, _SERVER_PORT, _MODEL)
    logger.info("Default CWD: %s | Memory DB: %s", _DEFAULT_CWD, MEMORY_DB)
    yield
    # Consolidate all active sessions on shutdown
    for sid, eng in _sessions.items():
        try: eng.stm.consolidate(ckey=eng.ckey)
        except Exception: pass
    logger.info("Server shutdown — all sessions consolidated to LTM.")

app = FastAPI(title="RAG Agent v5", version="5.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ── Pydantic models ───────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    task:         str
    ckey_api_key: str = ""

class ChatRequest(BaseModel):
    message:      str
    session_id:   str = "default"
    ckey_api_key: str = ""

class CwdRequest(BaseModel):
    session_id: str = "default"
    cwd: str

class ChatResetRequest(BaseModel):
    session_id: str = "default"

class FactRequest(BaseModel):
    key:        str
    value:      str
    category:   str = "general"
    session_id: str = "default"
    importance: float = 0.5

class RecallRequest(BaseModel):
    query:    str
    category: str = None
    limit:    int = 10

# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/")
async def health():
    return {
        "status": "ok", "version": "5.0.0", "model": _MODEL,
        "port": _SERVER_PORT, "default_cwd": _DEFAULT_CWD,
        "memory_db": str(MEMORY_DB),
        "modes": ["chatbot → POST /chat", "project → POST /run"],
        "memory": ["short-term (RAM window)", "long-term (SQLite episodes/facts/procedures)"],
    }

@app.get("/status")
async def server_status():
    active = sum(1 for t in _tasks.values() if t["status"] == "running")
    done   = sum(1 for t in _tasks.values() if t["status"] == "done")
    errors = sum(1 for t in _tasks.values() if t["status"] == "error")
    return {
        "active": active, "done": done, "errors": errors,
        "total": len(_tasks), "sessions": len(_sessions), "port": _SERVER_PORT,
    }

@app.get("/chat/activity/{session_id}")
async def get_activity(session_id: str, since: int = 0):
    """Return live action log so CLI can show what agent is doing mid-request."""
    log = _activity.get(session_id, [])
    return {"session_id": session_id, "entries": log[since:], "total": len(log)}

@app.get("/chat/progress/{session_id}")
async def get_chat_progress(session_id: str):
    """Return live supervisor progress (pct 0-100, phase, score) for UI progress bar."""
    prog = _chat_progress.get(session_id, {"pct": 0, "phase": "idle", "score": 0.0, "rounds": 0})
    return {"session_id": session_id, **prog}


# ── MODE 1: CHATBOT ───────────────────────────────────────────────────────────

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    sid  = req.session_id or "default"
    ckey = req.ckey_api_key or _DEFAULT_CKEY
    if sid not in _sessions:
        _sessions[sid] = ChatEngine(session_id=sid, ckey=ckey)
    engine = _sessions[sid]
    if ckey and not engine.ckey:
        engine.ckey = ckey
        engine.supervisor.ckey = ckey
    logger.info("[CHAT] session=%s msg=%r", sid, req.message[:80])
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, engine.chat, req.message)
        return {
            "session_id":       sid,
            "reply":            result["reply"],
            "history_len":      result["history_len"],
            "stm_turns":        result["stm_turns"],
            "cwd":              result["cwd"],
            "exec_count":       len(result["exec_results"]),
            "action_count":     len(result["action_results"]),
            "exec_results":     result["exec_results"],
            "action_results":   result["action_results"],
            "supervisor_rounds": result.get("supervisor_rounds", 0),
        }
    except Exception as exc:
        logger.exception("[CHAT] Error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})

@app.post("/chat/reset")
async def chat_reset(req: ChatResetRequest):
    sid = req.session_id or "default"
    if sid in _sessions:
        _sessions[sid].reset()
    return {"session_id": sid, "reset": True}

@app.post("/chat/cwd")
async def set_cwd(req: CwdRequest):
    sid = req.session_id or "default"
    p   = Path(req.cwd)
    if not p.exists():
        return JSONResponse(status_code=400,
                            content={"error": f"Directory not found: {req.cwd}"})
    _session_cwd[sid] = str(p)
    if sid in _sessions:
        _sessions[sid].stm.set_ctx("cwd", str(p))
    logger.info("[CWD] session=%s → %s", sid, p)
    return {"session_id": sid, "cwd": str(p)}

@app.get("/chat/cwd/{session_id}")
async def get_cwd_endpoint(session_id: str):
    return {"session_id": session_id, "cwd": get_cwd(session_id)}

@app.get("/chat/history/{session_id}")
async def chat_history(session_id: str):
    eng = _sessions.get(session_id)
    if not eng:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    return {
        "session_id":  session_id,
        "history":     [m for m in eng.history if m["role"] != "system"],
        "exec_log":    eng.exec_log,
        "action_log":  eng.action_log,
        "stm_turns":   eng.stm.turn_count,
        "working_ctx": eng.stm.working_ctx,
    }

@app.get("/chat/memory/{session_id}")
async def chat_memory(session_id: str):
    """Full memory snapshot: STM + LTM for this session."""
    eng = _sessions.get(session_id)
    if not eng:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    return eng.memory_snapshot()

@app.get("/chat/sessions")
async def chat_sessions():
    return {"sessions": [
        {"id": s, "stm_turns": e.stm.turn_count,
         "total_turns": e.stm.turn_count, "cwd": get_cwd(s)}
        for s, e in _sessions.items()
    ]}

# ── Memory API (direct LTM access) ───────────────────────────────────────────

@app.post("/memory/fact")
async def store_fact(req: FactRequest):
    _LTM.remember_fact(req.key, req.value, req.category,
                       req.session_id, req.importance)
    return {"stored": True, "key": req.key}

@app.get("/memory/facts")
async def get_facts(category: str = None, limit: int = 50):
    return {"facts": _LTM.list_facts(category=category, limit=limit)}

@app.post("/memory/recall")
async def recall_facts(req: RecallRequest):
    facts = _LTM.recall_facts(req.query, req.category, req.limit)
    return {"query": req.query, "facts": facts}

@app.get("/memory/episodes/{session_id}")
async def get_episodes(session_id: str, limit: int = 10):
    return {"episodes": _LTM.get_recent_episodes(session_id, limit=limit)}

@app.get("/memory/stats")
async def memory_stats():
    return {
        "ltm_stats":  _LTM.get_stats(),
        "task_stats": _LTM.get_task_stats(),
        "recent_tasks": _LTM.get_recent_tasks(limit=8),
    }

@app.get("/memory/consolidate/{session_id}")
async def force_consolidate(session_id: str):
    """Force STM → LTM consolidation for a session."""
    eng = _sessions.get(session_id)
    if not eng:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, eng.stm.consolidate)
    return {"consolidated": True, "session_id": session_id}

# ── Quick file tools ──────────────────────────────────────────────────────────

@app.get("/fs/list")
async def fs_list(path: str = _DEFAULT_CWD):
    return list_dir(path)

@app.get("/fs/read")
async def fs_read(path: str):
    return read_file(path)

@app.post("/fs/shell")
async def fs_shell(cmd: str, cwd: str = _DEFAULT_CWD):
    return execute_shell(cmd, cwd=cwd)

# ── Quick system control tools ────────────────────────────────────────────────

@app.post("/system/screenshot")
async def api_screenshot(path: str = None):
    return take_screenshot(save_path=path)

@app.post("/system/mouse/move")
async def api_mouse_move(x: int, y: int, duration: float = 0.3):
    return mouse_move(x, y, duration)

@app.post("/system/mouse/click")
async def api_mouse_click(x: int, y: int, button: str = "left", clicks: int = 1):
    return mouse_click(x, y, button, clicks)

@app.post("/system/keyboard/press")
async def api_key_press(keys: str):
    return key_press(keys)

@app.post("/system/keyboard/type")
async def api_type_text(text: str, interval: float = 0.05):
    return type_text(text, interval)

# ── MODE 2: PROJECT AGENT ─────────────────────────────────────────────────────

@app.post("/run")
async def run_project(req: RunRequest):
    task_id = f"task_{int(time.time()*1000)}_{len(_tasks)}"
    record  = {
        "task_id": task_id, "task": req.task, "mode": "project",
        "status": "running", "phase": "Initialising", "pct": 0,
        "fix_attempt": 0, "fix_status": None, "submitted_at": time.time(),
    }
    _tasks[task_id] = record
    ckey = req.ckey_api_key or _DEFAULT_CKEY
    asyncio.get_event_loop().run_in_executor(
        _executor, _run_project_task, task_id, req.task, record, ckey)
    logger.info("[PROJECT] Submitted %s | %r", task_id, req.task[:60])
    return {"task_id": task_id, "status": "running"}

@app.get("/task/{task_id}")
async def get_task(task_id: str):
    t = _tasks.get(task_id)
    return t if t else JSONResponse(status_code=404, content={"error": "not found"})

@app.get("/tasks")
async def list_tasks(limit: int = 50):
    return {"tasks": list(_tasks.values())[-limit:], "total": len(_tasks)}

# ── Logs ──────────────────────────────────────────────────────────────────────

@app.get("/logs")
async def get_logs(lines: int = 50):
    if not SERVER_LOG.exists():
        return {"lines": [], "log_file": str(SERVER_LOG)}
    all_lines = SERVER_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": all_lines[-lines:], "total": len(all_lines),
            "log_file": str(SERVER_LOG)}

# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await ws.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"mode": "chat", "message": data.strip()}

            mode = payload.get("mode", "chat")
            if mode == "chat":
                sid = payload.get("session_id", "default")
                msg = payload.get("message", "")
                if sid not in _sessions:
                    _sessions[sid] = ChatEngine(session_id=sid)
                loop   = asyncio.get_event_loop()
                result = await loop.run_in_executor(_executor, _sessions[sid].chat, msg)
                await ws.send_json({
                    "mode": "chat", "session_id": sid,
                    "reply": result["reply"], "cwd": result["cwd"],
                    "exec_count": len(result["exec_results"]),
                    "stm_turns": result["stm_turns"],
                })
            else:
                task    = payload.get("task", "")
                task_id = f"task_{int(time.time()*1000)}_{len(_tasks)}"
                record  = {
                    "task_id": task_id, "task": task, "mode": "project",
                    "status": "running", "phase": "Initialising", "pct": 0,
                    "fix_attempt": 0, "submitted_at": time.time(),
                }
                _tasks[task_id] = record
                await ws.send_json({"task_id": task_id, "status": "running"})
                loop   = asyncio.get_event_loop()
                future = loop.run_in_executor(
                    _executor, _run_project_task, task_id, task, record)
                while not future.done():
                    await asyncio.sleep(2)
                    cur = _tasks.get(task_id, {})
                    await ws.send_json({
                        "task_id": task_id, "status": cur.get("status"),
                        "phase": cur.get("phase"), "pct": cur.get("pct"),
                    })
                await ws.send_json(_tasks.get(task_id, {}))

    except WebSocketDisconnect:
        logger.info("[WS] Disconnected")
    except Exception as exc:
        logger.exception("[WS] Error: %s", exc)

# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("RAG Agent Server v5.0")
    logger.info("  Host        : %s", _SERVER_HOST)
    logger.info("  Port        : %d", _SERVER_PORT)
    logger.info("  Model       : %s", _MODEL)
    logger.info("  Default CWD : %s", _DEFAULT_CWD)
    logger.info("  Memory DB   : %s", MEMORY_DB)
    logger.info("  STM Window  : %d turns", STM_WINDOW)
    logger.info("  Consolidate : every %d turns", STM_CONSOLIDATE_EVERY)
    logger.info("  Chatbot     : POST /chat  (real execution)")
    logger.info("  Project     : POST /run   (full pipeline)")
    logger.info("=" * 60)
    uvicorn.run("server:app", host=_SERVER_HOST, port=_SERVER_PORT,
                reload=False, log_level="info")