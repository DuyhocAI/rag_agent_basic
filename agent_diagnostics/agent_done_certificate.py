# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====


import json
from datetime import datetime
from pathlib import Path


class DoneCertificate:
    def __init__(self, memory_dir="memory"):
        # Thu muc luu certificate
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.memory_dir / "task_done_certificate.json"

    def clear(self):
        # Xoa certificate cu truoc khi bat dau task moi
        if self.path.exists():
            self.path.unlink()

    def issue(
        self,
        task,
        final_answer,
        iterations,
        self_debate_result,
        guard_result,
        recent_tool_results
    ):
        # Cap certificate khi task da duoc xac minh hoan thanh
        payload = {
            "issued_at": datetime.now().isoformat(),
            "task": task,
            "final_answer": final_answer,
            "iterations": iterations,
            "self_debate_result": self_debate_result,
            "guard_result": guard_result,
            "recent_tool_result_count": len(recent_tool_results or []),
            "verified_complete": True
        }

        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        return payload

    def exists(self):
        # Kiem tra certificate co ton tai khong
        return self.path.exists()

    def load(self):
        # Doc certificate
        if not self.path.exists():
            return None

        try:
            return json.loads(
                self.path.read_text(encoding="utf-8", errors="ignore")
            )
        except Exception:
            return None
