# -*- coding: utf-8 -*-
# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys

_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent.parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

import socket
import time
import fix_encoding
from agent_core.cli_auto import main


def test_cli_auto_help_imports():
    assert callable(main)


def test_socket_available():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.close()


def test_fix_encoding_module_loaded():
    assert fix_encoding is not None


def test_main_exists():
    assert callable(main)


def test_timing_basic():
    start = time.time()
    time.sleep(0.01)
    assert time.time() >= start