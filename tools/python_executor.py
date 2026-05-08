# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
Tool thuc thi code Python de tra loi cau hoi truc tiep
"""
import subprocess
import sys
import tempfile
import os
from pathlib import Path


class PythonExecutor:
    """Thuc thi code Python trong moi truong an toan"""
    
    def __init__(self):
        self.timeout = 30  # giay
    
    def execute(self, code: str, context: str = "") -> dict:
        """
        Thuc thi code Python va tra ve ket qua
        
        Args:
            code: Code Python can thuc thi
            context: Noi dung bo sung (neu can)
        
        Returns:
            dict voi keys: success, output, error
        """
        try:
            # Tao file tam
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(code)
                temp_file = f.name
            
            # Thuc thi code
            result = subprocess.run(
                [sys.executable, temp_file],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding='utf-8',
                errors='replace'
            )
            
            # Xoa file tam
            os.unlink(temp_file)
            
            if result.returncode == 0:
                return {
                    "success": True,
                    "output": result.stdout,
                    "error": None
                }
            else:
                return {
                    "success": False,
                    "output": result.stdout,
                    "error": result.stderr
                }
        
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": f"Code execution timeout after {self.timeout}s"
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": str(e)
            }
    
    def execute_and_get_result(self, code: str) -> str:
        """
        Thuc thi code va tra ve output duoi dang string
        """
        result = self.execute(code)
        if result["success"]:
            return result["output"]
        else:
            return f"Error: {result['error']}"
