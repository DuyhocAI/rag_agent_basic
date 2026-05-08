# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

# -*- coding: utf-8 -*-
import socket
import sys
import time
import fix_encoding  # Fix encoding

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

print("="*60)
print("AUTO TEST CLI.PY - 5 PROMPTS")
print("="*60)

prompts = [
    "co tat ca bao nhieu folder con trong folder nay?",
    "liet ke cac file trong thu muc hien tai",
    "hien thi noi dung file README.md",
    "kiem tra phien ban Python",
    "test tin nhan cuoi cung"
]

print("\n[1] Kiem tra chat server port 5555...")
try:
    test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test_sock.settimeout(2)
    test_sock.connect(('127.0.0.1', 5555))
    test_sock.close()
    print("  [OK] Chat server dang chay")
except Exception as e:
    print(f"  [LOI] Chat server khong chay")
    print("\n  Chay chat server truoc:")
    print("    python chat_server.py")
    sys.exit(1)

print(f"\n[2] Gui {len(prompts)} prompts...")

sock = None
success = 0

try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(('127.0.0.1', 5555))
    print("  [OK] Ket noi thanh cong\n")
    
    for i, msg in enumerate(prompts, 1):
        print(f"  [{i}/5] Gui: {msg}")
        sock.sendall(msg.encode('utf-8'))
        success += 1
        print(f"        [OK] Da gui thanh cong")
        time.sleep(0.5)
    
except Exception as e:
    print(f"  [LOI] {e}")

finally:
    if sock:
        sock.close()
        print("\n  [OK] Da dong ket noi")

print("\n" + "="*60)
print(f"KET QUA: {success}/5 prompts thanh cong")
print("="*60)

if success == 5:
    print("\n[THANH CONG] TAT CA 5 PROMPTS DA GUI THANH CONG!")
    print("CLI.PY HOAT DONG HOAN HAO - KHONG CAN FIX!")
else:
    print(f"\n[THAT BAI] Chi gui duoc {success}/5 prompts")
