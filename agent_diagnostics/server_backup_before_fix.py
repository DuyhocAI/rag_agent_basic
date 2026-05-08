# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
rag_agent/server.py
FastAPI server exposing the RAG Agent over HTTP + WebSocket.
"""

import asyncio
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional
import fix_encoding  # Fix encoding

# Xu ly loi ModuleNotFoundError khi chay truc tiep file
# Tu dong day thu muc cha (C:\Bao_Duy) vao PYTHONPATH de nhan dien package 'rag_agent'
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Logging setup — Ghi vao dung muc tieu duoc yeu cau kem theo viec xoa file cu
# ---------------------------------------------------------------------------

LOG_DIR = Path(r"C:\Bao_Duy\rag_agent")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "server.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        # Set tham so mode='w' de lam sach du lieu cu trong lan khoi dong moi
        logging.FileHandler(LOG_FILE, mode='w', encoding="utf-8"),
    ],
)
logger = logging.getLogger("rag_agent")

# ---------------------------------------------------------------------------
# In-memory task registry
# ---------------------------------------------------------------------------

_tasks: dict[str, dict[str, Any]] = {}
_executor = ThreadPoolExecutor(max_workers=4)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("RAG Agent server starting up …")
    yield

app = FastAPI(title="RAG Agent", version="1.0.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    task: str

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    cfg_path = Path("config.json")
    if not cfg_path.exists():
        return {}
    with open(cfg_path) as f:
        return json.load(f)

def _get_agent():
    # Co che fallback de dam bao import thanh cong trong moi truong hop
    try:
        from core.agent import RAGAgent
from core.qa_agent import QuestionAnsweringAgent
    except ModuleNotFoundError:
        from core.agent import RAGAgent
    return RAGAgent()

def _run_task_sync(task_id: str, task: str) -> None:
    try:
        agent = _get_agent()
        
        # Phan biet giua cau hoi truc tiep va yeu cau tao code
        # Neu task ngan va dang cau hoi, dung QA agent
        is_question = any(keyword in task.lower() for keyword in [
            'bao nhieu', 'how many', 'what is', 'la gi', 'co phai', 'is there',
            'list', 'show', 'hien thi', 'dem', 'count', 'tinh', 'calculate'
        ]) and len(task.split()) < 20
        
        if is_question:
            # Dung QA agent de tra loi truc tiep
            qa_agent = QuestionAnsweringAgent(agent)
            answer = qa_agent.answer_question(task)
            _tasks[task_id].update({
                "status": "done",
                "result": {"answer": answer, "type": "qa"},
                "finished_at": time.time()
            })
        else:
            # Dung agent binh thuong de tao code
            meta = agent.run(task)
            _tasks[task_id].update({
                "status": "done",
                "result": meta,
                "finished_at": time.time()
            })
    except Exception as exc:  # noqa: BLE001
        logger.exception("Task %s failed", task_id)
        _tasks[task_id].update({"status": "error", "error": str(exc), "finished_at": time.time()})

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def health():
    return {"status": "ok", "service": "rag_agent", "time": time.strftime("%Y-%m-%dT%H:%M:%SZ")}

@app.get("/status")
async def status():
    active = sum(1 for t in _tasks.values() if t["status"] == "running")
    done = sum(1 for t in _tasks.values() if t["status"] == "done")
    errors = sum(1 for t in _tasks.values() if t["status"] == "error")
    return {"active": active, "done": done, "errors": errors, "total": len(_tasks)}

@app.post("/run")
async def run_task(req: RunRequest):
    task_id = f"task_{int(time.time())}_{len(_tasks)}"
    _tasks[task_id] = {
        "task_id": task_id,
        "task": req.task,
        "status": "running",
        "submitted_at": time.time(),
    }
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_task_sync, task_id, req.task)
    logger.info("Task %s submitted | task=%r", task_id, req.task[:60])
    return {"task_id": task_id, "status": "running"}

@app.get("/task/{task_id}")
async def get_task(task_id: str):
    task = _tasks.get(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": "task not found"})
    return task

@app.get("/logs")
async def get_logs(lines: int = 50):
    if not LOG_FILE.exists():
        return {"lines": []}
    with open(LOG_FILE, encoding="utf-8") as f:
        all_lines = f.readlines()
    return {"lines": all_lines[-lines:]}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await ws.receive_text()
            try:
                payload = json.loads(data)
                task = payload.get("task", "")
            except json.JSONDecodeError:
                task = data

            if not task:
                await ws.send_json({"error": "empty task"})
                continue

            task_id = f"task_{int(time.time())}_{len(_tasks)}"
            _tasks[task_id] = {
                "task_id": task_id,
                "task": task,
                "status": "running",
                "submitted_at": time.time(),
            }

            await ws.send_json({"task_id": task_id, "status": "running"})

            loop = asyncio.get_event_loop()
            future = loop.run_in_executor(_executor, _run_task_sync, task_id, task)

            while not future.done():
                await asyncio.sleep(2)
                current = _tasks.get(task_id, {})
                await ws.send_json({"task_id": task_id, "status": current.get("status", "running")})

            final = _tasks.get(task_id, {})
            await ws.send_json(final)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = _load_config()
    host = cfg.get("server", {}).get("host", "0.0.0.0")
    # Dat co dinh port la 8765
    port = cfg.get("server", {}).get("port", 8765)
    logger.info("Starting uvicorn on %s:%d", host, port)
    uvicorn.run("server:app", host=host, port=port, reload=False)