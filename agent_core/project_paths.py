# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====


from pathlib import Path


def project_root():
    # Tra ve thu muc root cua project
    return Path(__file__).resolve().parents[1]


def ensure_dir(path):
    # Tao folder neu chua co
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


ROOT = project_root()
LOGS_DIR = ensure_dir(ROOT / "logs")
REPORTS_DIR = ensure_dir(ROOT / "reports")
BACKUP_DIR = ensure_dir(ROOT / "backup")
MEMORY_DIR = ensure_dir(ROOT / "memory")


def log_path(name):
    # Lay duong dan file log trong logs/
    return LOGS_DIR / name


def report_path(name):
    # Lay duong dan report trong reports/
    return REPORTS_DIR / name


def backup_path(name):
    # Lay duong dan backup trong backup/
    return BACKUP_DIR / name


def memory_path(name):
    # Lay duong dan memory trong memory/
    return MEMORY_DIR / name


def root_path(name):
    # Lay duong dan trong root project
    return ROOT / name
