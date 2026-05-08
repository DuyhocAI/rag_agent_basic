# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
Memory Manager - Quản lý 3 lớp memory của RAG-Agent
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Any

class MemoryManager:
    def __init__(self, memory_dir: str = "memory"):
        self.memory_dir = memory_dir
        self.core_path = os.path.join(memory_dir, "core_memory.json")
        self.conversation_path = os.path.join(memory_dir, "conversation_history.json")
        self.task_path = os.path.join(memory_dir, "task_memory.json")
        
        # Tạo thư mục nếu chưa có
        os.makedirs(memory_dir, exist_ok=True)
    
    def _load_json(self, path: str) -> Dict:
        """Đọc file JSON"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def _save_json(self, path: str, data: Dict):
        """Ghi file JSON"""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    # === CORE MEMORY ===
    def get_core_memory(self) -> Dict:
        """Lấy core memory"""
        return self._load_json(self.core_path)
    
    def update_user_preference(self, key: str, value: Any):
        """Cập nhật preference của user"""
        core = self.get_core_memory()
        core["user_profile"]["preferences"][key] = value
        self._save_json(self.core_path, core)
    
    def add_important_fact(self, fact: str):
        """Thêm fact quan trọng cần nhớ"""
        core = self.get_core_memory()
        core["important_facts"].append({
            "fact": fact,
            "timestamp": datetime.now().isoformat()
        })
        self._save_json(self.core_path, core)
    
    # === CONVERSATION HISTORY ===
    def add_message(self, role: str, content: str):
        """Thêm message vào lịch sử (tự động sliding window)"""
        conv = self._load_json(self.conversation_path)
        
        conv["messages"].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        
        # Sliding window: giữ tối đa max_messages
        max_msg = conv.get("max_messages", 50)
        if len(conv["messages"]) > max_msg:
            conv["messages"] = conv["messages"][-max_msg:]
        
        conv["current_count"] = len(conv["messages"])
        self._save_json(self.conversation_path, conv)
    
    def get_recent_messages(self, n: int = 10) -> List[Dict]:
        """Lấy n message gần nhất"""
        conv = self._load_json(self.conversation_path)
        return conv.get("messages", [])[-n:]
    
    # === TASK MEMORY ===
    def log_task(self, task_description: str, result: str, files_modified: List[str] = None):
        """Ghi nhận task đã hoàn thành"""
        task_mem = self._load_json(self.task_path)
        
        task_mem["completed_tasks"].append({
            "description": task_description,
            "result": result,
            "files_modified": files_modified or [],
            "timestamp": datetime.now().isoformat()
        })
        
        # Cập nhật file_modifications
        if files_modified:
            for file in files_modified:
                task_mem["file_modifications"].append({
                    "file": file,
                    "task": task_description,
                    "timestamp": datetime.now().isoformat()
                })
        
        self._save_json(self.task_path, task_mem)
    
    def log_error(self, error_type: str, error_msg: str, solution: str = None):
        """Ghi nhận lỗi và cách giải quyết"""
        task_mem = self._load_json(self.task_path)
        
        task_mem["errors_encountered"].append({
            "type": error_type,
            "message": error_msg,
            "solution": solution,
            "timestamp": datetime.now().isoformat()
        })
        
        self._save_json(self.task_path, task_mem)
    
    def learn_pattern(self, pattern_name: str, pattern_data: Dict):
        """Học pattern mới từ kinh nghiệm"""
        task_mem = self._load_json(self.task_path)
        
        if "learned_patterns" not in task_mem:
            task_mem["learned_patterns"] = {}
        
        task_mem["learned_patterns"][pattern_name] = {
            "data": pattern_data,
            "learned_at": datetime.now().isoformat()
        }
        
        self._save_json(self.task_path, task_mem)
    
    # === CONTEXT BUILDING ===
    def build_context(self) -> str:
        """Xây dựng context đầy đủ để đưa vào prompt"""
        core = self.get_core_memory()
        recent_msgs = self.get_recent_messages(5)
        task_mem = self._load_json(self.task_path)
        
        context = f"""
=== CORE MEMORY ===
User: {core.get('user_profile', {}).get('name', 'Unknown')}
Preferences: {core.get('user_profile', {}).get('preferences', {})}
Important Facts: {core.get('important_facts', [])}

=== RECENT CONVERSATION ===
{self._format_messages(recent_msgs)}

=== TASK HISTORY ===
Completed Tasks: {len(task_mem.get('completed_tasks', []))}
Recent Errors: {len(task_mem.get('errors_encountered', []))}
Learned Patterns: {list(task_mem.get('learned_patterns', {}).keys())}
"""
        return context
    
    def _format_messages(self, messages: List[Dict]) -> str:
        """Format messages thành text"""
        return "\n".join([
            f"[{msg['role']}]: {msg['content'][:100]}..."
            for msg in messages
        ])

# === USAGE EXAMPLE ===
if __name__ == "__main__":
    mm = MemoryManager()
    
    # Test core memory
    mm.add_important_fact("User thích code Python và comment tiếng Việt")
    
    # Test conversation
    mm.add_message("user", "Tạo hệ thống memory cho tôi")
    mm.add_message("assistant", "Đã tạo xong hệ thống memory 3 lớp")
    
    # Test task memory
    mm.log_task(
        "Tạo hệ thống memory",
        "Thành công",
        ["memory/core_memory.json", "memory/memory_manager.py"]
    )
    
    # Build context
    print(mm.build_context())