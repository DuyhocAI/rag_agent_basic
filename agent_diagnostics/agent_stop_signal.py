# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====


import sys
import threading
import time


class StopSignal:
    def __init__(self):
        # Co dung de yeu cau agent dung lai
        self.stop_requested = False
        self.reason = None
        self._thread = None

    def request_stop(self, reason="Nguoi dung yeu cau dung"):
        # Dat co dung
        self.stop_requested = True
        self.reason = reason

    def start_ctrl_q_listener(self):
        # Khoi dong listener Ctrl+Q
        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self._listen_ctrl_q,
            daemon=True
        )
        self._thread.start()

    def _listen_ctrl_q(self):
        # Lang nghe Ctrl+Q tren Windows hoac Unix
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

                    # Ctrl+Q = ASCII 17 = \x11
                    if ch == "\x11":
                        self.request_stop("Nguoi dung an Ctrl+Q")
                        break

                time.sleep(0.05)

        except Exception as e:
            self.request_stop(f"Loi listener Ctrl+Q Windows: {e}")

    def _listen_unix(self):
        # Bat Ctrl+Q tren Unix/Linux/macOS
        # Luu y: mot so terminal nuot Ctrl+Q do flow control.
        # Neu can, chay: stty -ixon
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

                        # Ctrl+Q = ASCII 17 = \x11
                        if ch == "\x11":
                            self.request_stop("Nguoi dung an Ctrl+Q")
                            break

            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        except Exception:
            # Neu khong bat duoc Ctrl+Q thi bo qua, khong lam hong agent
            pass
