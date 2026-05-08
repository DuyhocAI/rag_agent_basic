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
import requests
from pathlib import Path


class AgentServerManager:
    def __init__(
        self,
        base_url="http://127.0.0.1:8000",
        server_script="server.py",
        startup_timeout=30
    ):
        # Quan ly server local
        self.base_url = base_url.rstrip("/")
        self.server_script = server_script
        self.startup_timeout = startup_timeout
        self.process = None
        self.log_file = None

    def is_server_alive(self):
        # Kiem tra server co phan hoi khong
        try:
            response = requests.get(
                self.base_url,
                timeout=3
            )
            return response.status_code < 500
        except Exception:
            return False

    def start_if_needed(self):
        # Khoi dong server neu chua chay
        if self.is_server_alive():
            print(f"Server da dang chay: {self.base_url}")
            return False

        if not Path(self.server_script).exists():
            raise FileNotFoundError(f"Khong tim thay {self.server_script}")

        print(f"Server chua chay. Dang khoi dong {self.server_script}...")

        self.log_file = open(
            "server_autonomous_runtime.log",
            "w",
            encoding="utf-8",
            errors="ignore"
        )

        self.process = subprocess.Popen(
            [sys.executable, self.server_script],
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )

        start_time = time.time()

        while time.time() - start_time < self.startup_timeout:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"server.py da thoat som voi returncode={self.process.returncode}"
                )

            if self.is_server_alive():
                print("Server da san sang.")
                return True

            time.sleep(1)

        raise TimeoutError("Het thoi gian cho server khoi dong")

    def stop_if_started_by_me(self):
        # Dung server neu do manager khoi dong
        if self.process is None:
            return

        if self.process.poll() is None:
            print("Dang dung server do manager khoi dong...")
            self.process.terminate()

            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

        if self.log_file is not None:
            try:
                self.log_file.close()
            except Exception:
                pass
