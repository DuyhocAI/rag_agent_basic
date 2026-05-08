# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

# -*- coding: utf-8 -*-
import logging
import os
import sys
from datetime import datetime

# Xu ly encoding cho Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def setup_logger(name, log_file, level=logging.INFO):
    """
    Thiet lap logger voi co che xoa log cu khi khoi dong phien moi
    """
    # Xoa noi dung file log cu neu ton tai
    if os.path.exists(log_file):
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write('')
    
    # Tao formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Tao file handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    # Tao console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # Tao logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()
    
    # Them handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Ghi log khoi dong phien moi
    logger.info("="*60)
    logger.info(f"BAT DAU PHIEN MOI - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("="*60)
    
    return logger
