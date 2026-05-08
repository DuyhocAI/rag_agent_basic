# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

import urllib.request
import urllib.error
import json
import os
import subprocess
import shutil
import traceback

# Cau hinh ket noi API
URL = 'https://ckey.vn/v1/chat/completions'
API_KEY = 'sk-93bfdf1cb100ed0379a5d5724f83af7621462aafd9037c7a318c01a180e2f860'
MEMORY_FILE = 'agent_memory.json'

# Gioi han token de tranh bung no lich su
MAX_HISTORY_MESSAGES = 20       # Giu toi da 20 tin nhan gan nhat
SUMMARIZE_THRESHOLD = 15        # Bat dau tom tat khi vuot 15 tin nhan


class RAGAgent:
    def __init__(self):
        self.history = self.load_memory()
        if not isinstance(self.history, list):
            self.history = []

        self.system_prompt = {
            "role": "system",
            "content": (
                "Ban la mot He thong Agent AI tu chu cap cao, chuyen gia ve Lap trinh va Khoa hoc Du lieu.\n\n"

                "KIEN TRUC QUAN TRONG - DOC KY:\n"
                "Ban dang chay ben trong mot chuong trinh Python tren may tinh cua nguoi dung. "
                "Khi ban phat lenh ```read_file```, ```write_file```, ```list_dir```, v.v., "
                "chuong trinh Python do se THUC TIEN thuc thi lenh do TREN MAY TINH CUA NGUOI DUNG "
                "va tra ket qua ve cho ban qua tin nhan [system]. "
                "Vi vay ban CO DAY DU QUYEN TRUY CAP vao filesystem cua nguoi dung. "
                "TUYET DOI KHONG BAO GIO noi 'toi khong the doc file', 'HTTP 403', "
                "'toi khong co quyen', hoac yeu cau nguoi dung tu copy/paste noi dung file. "
                "Neu can doc file, HAY GOI LUON LENH ```read_file``` va cho ket qua tu [system].\n\n"

                "QUY TRINH XU LY YEU CAU:\n"
                "1. Nguoi dung yeu cau -> Ban goi lenh filesystem / Python -> "
                "[system] tra ket qua thuc te -> Ban phan tich va hanh dong tiep.\n"
                "2. Neu ket qua tra ve loi (file khong ton tai, sai duong dan...), "
                "hay thu ```list_dir``` thu muc cha de tim dung ten file, roi doc lai.\n"
                "3. Lap lai cho den khi hoan thanh nhiem vu, khong hoi nguoi dung.\n\n"

                "CONG CU FILESYSTEM (cu phap chinh xac):\n"
                "Doc file      : ```read_file\n[duong_dan_day_du]\n```\n"
                "Ghi file      : ```write_file\n[duong_dan_day_du]\n[noi_dung]\n```\n"
                "Them vao file : ```append_file\n[duong_dan_day_du]\n[noi_dung]\n```\n"
                "Liet ke       : ```list_dir\n[duong_dan_day_du]\n```\n"
                "Xoa file      : ```delete_file\n[duong_dan_day_du]\n```\n"
                "Tao thu muc   : ```mkdir\n[duong_dan_day_du]\n```\n"
                "Di chuyen     : ```move_file\n[nguon] [dich]\n```\n"
                "Sao chep      : ```copy_file\n[nguon] [dich]\n```\n\n"

                "CONG CU KHAC:\n"
                "- Ma Python: ```python\n[code]\n```\n"
                "- Viet comment code bang tieng Viet.\n"
                "- Khong dung icon trong phan hoi.\n"
                "- Sau khi nhan ket qua [system], phan tich va hanh dong tiep ngay, "
                "khong hoi lai nguoi dung neu khong can thiet.\n\n"

                "NHIEM VU:\n"
                "1. Doc/ghi/sua file theo yeu cau.\n"
                "2. Viet va chay code Python de giai quyet van de.\n"
                "3. Voi du an AI: theo doi Accuracy/Loss/F1, tu dong toi uu neu hieu suat thap.\n"
                "4. Dung matplotlib/networkx ve bieu do neu can.\n"
                "5. Luon kiem tra thu muc hien tai truoc khi tao file moi."
            )
        }

    # ------------------------------------------------------------------ #
    #  Bo nho                                                              #
    # ------------------------------------------------------------------ #

    def load_memory(self):
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except:
                return []
        return []

    def save_memory(self):
        try:
            with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Loi khi luu bo nho: {e}")

    # ------------------------------------------------------------------ #
    #  Quan ly token / lich su                                             #
    # ------------------------------------------------------------------ #

    def trim_history(self):
        """
        Giu lich su trong gioi han MAX_HISTORY_MESSAGES.
        Neu vuot SUMMARIZE_THRESHOLD, gom cac tin nhan cu thanh 1 ban tom tat.
        """
        if len(self.history) <= SUMMARIZE_THRESHOLD:
            return

        # Tach phan cu (can tom tat) va phan moi (giu nguyen)
        keep_recent = MAX_HISTORY_MESSAGES // 2
        old_messages = self.history[:-keep_recent]
        recent_messages = self.history[-keep_recent:]

        # Tao ban tom tat bang cach goi API voi yeu cau ngan gon
        summary_prompt = [
            {
                "role": "user",
                "content": (
                    "Tom tat ngan gon nhung diem chinh cua cuoc tro chuyen sau day "
                    "trong khong qua 300 tu. Chi giu lai thong tin quan trong nhat:\n\n"
                    + "\n".join(
                        f"[{m['role'].upper()}]: {m['content'][:300]}"
                        for m in old_messages
                    )
                )
            }
        ]

        print("\n[He thong] Dang tom tat lich su cu de tiet kiem token...")
        summary_text = self.call_api(summary_prompt, use_history=False)

        # Thay the lich su cu bang ban tom tat
        summary_message = {
            "role": "system",
            "content": f"[TOM TAT LICH SU TRUOC DO]: {summary_text}"
        }
        self.history = [summary_message] + recent_messages
        print(f"[He thong] Da giam lich su xuong con {len(self.history)} tin nhan.")

    # ------------------------------------------------------------------ #
    #  Goi API                                                             #
    # ------------------------------------------------------------------ #

    def call_api(self, messages, use_history=True):
        payload = {
            'model': 'claude-opus-4-6',
            'max_tokens': 8000,
            'messages': [self.system_prompt] + messages
        }

        data = json.dumps(payload).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {API_KEY}',
            'User-Agent': 'Mozilla/5.0'
        }

        req = urllib.request.Request(URL, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                return res_data['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            err = e.read().decode('utf-8')
            return f"Loi HTTP tu Server: {err}"
        except Exception as e:
            return f"Loi ket noi: {str(e)}"

    # ------------------------------------------------------------------ #
    #  Thuc thi Python                                                     #
    # ------------------------------------------------------------------ #

    def execute_python(self, code):
        print("\n--- He thong dang thuc thi code Python ---")
        temp_file = "agent_runtime_script.py"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(code)

            result = subprocess.run(
                ['python', temp_file],
                capture_output=True, text=True, timeout=120
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            if result.returncode == 0:
                return f"Thuc thi thanh cong. Output:\n{stdout}"
            else:
                return f"Thuc thi bi loi. Chi tiet:\n{stderr}"
        except Exception as e:
            return f"Loi he thong khi chay code: {str(e)}"
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

    # ------------------------------------------------------------------ #
    #  Cong cu thao tac file / thu muc                                     #
    # ------------------------------------------------------------------ #

    def fs_list_dir(self, path):
        """Liet ke noi dung thu muc."""
        try:
            path = path.strip() or '.'
            items = os.listdir(path)
            result_lines = []
            for item in sorted(items):
                full = os.path.join(path, item)
                kind = '[DIR] ' if os.path.isdir(full) else '[FILE]'
                size = os.path.getsize(full) if os.path.isfile(full) else '-'
                result_lines.append(f"{kind} {item}  ({size} bytes)")
            return f"Thu muc: {os.path.abspath(path)}\n" + "\n".join(result_lines)
        except Exception as e:
            return f"Loi list_dir: {e}"

    def fs_read_file(self, path):
        """Doc noi dung file van ban."""
        try:
            path = path.strip()
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            return f"Noi dung file '{path}':\n{content}"
        except Exception as e:
            return f"Loi read_file: {e}"

    def fs_write_file(self, path, content):
        """Ghi noi dung vao file (ghi de)."""
        try:
            path = path.strip()
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Da ghi thanh cong vao '{path}' ({len(content)} ky tu)."
        except Exception as e:
            return f"Loi write_file: {e}"

    def fs_append_file(self, path, content):
        """Them noi dung vao cuoi file."""
        try:
            path = path.strip()
            with open(path, 'a', encoding='utf-8') as f:
                f.write(content)
            return f"Da them thanh cong vao '{path}'."
        except Exception as e:
            return f"Loi append_file: {e}"

    def fs_delete_file(self, path):
        """Xoa file."""
        try:
            path = path.strip()
            os.remove(path)
            return f"Da xoa file '{path}'."
        except Exception as e:
            return f"Loi delete_file: {e}"

    def fs_mkdir(self, path):
        """Tao thu muc."""
        try:
            os.makedirs(path.strip(), exist_ok=True)
            return f"Da tao thu muc '{path}'."
        except Exception as e:
            return f"Loi mkdir: {e}"

    def fs_move_file(self, src, dst):
        """Di chuyen / doi ten file."""
        try:
            shutil.move(src.strip(), dst.strip())
            return f"Da di chuyen '{src}' -> '{dst}'."
        except Exception as e:
            return f"Loi move_file: {e}"

    def fs_copy_file(self, src, dst):
        """Sao chep file."""
        try:
            shutil.copy2(src.strip(), dst.strip())
            return f"Da sao chep '{src}' -> '{dst}'."
        except Exception as e:
            return f"Loi copy_file: {e}"

    # ------------------------------------------------------------------ #
    #  Xu ly lenh tu phan hoi cua AI                                       #
    # ------------------------------------------------------------------ #

    def _extract_blocks(self, response, cmd):
        """
        Trich xuat tat ca khoi lenh ```<cmd> ... ``` tu chuoi response.
        Ho tro ca truong hop AI khong dong ``` hoac xuong dong sau ten lenh.
        Tra ve danh sach cac chuoi noi dung ben trong khoi.
        """
        import re
        # Match: ```cmd\n...content...\n``` hoac ```cmd content``` (khong co dong dong)
        pattern = re.compile(
            r'```' + re.escape(cmd) + r'[ \t]*\n(.*?)(?:```|$)',
            re.DOTALL
        )
        blocks = pattern.findall(response)

        # Du phong: neu AI viet ```cmd duong/dan (khong xuong dong)
        if not blocks:
            pattern2 = re.compile(r'```' + re.escape(cmd) + r'[ \t]+([^\n`]+)')
            blocks = pattern2.findall(response)

        return blocks

    def _parse_two_paths(self, text):
        """
        Tach 2 duong dan tu 1 dong van ban.
        Ho tro duong dan co khoang trang neu duoc bao trong ngoac kep.
        Vi du: "C:\\My Folder\\a.txt" "C:\\My Folder\\b.txt"
        Hoac khong co ngoac kep: C:\\foo\\a.txt C:\\bar\\b.txt
        """
        import shlex
        try:
            tokens = shlex.split(text.replace('\\', '/'))
            if len(tokens) >= 2:
                # Khoi phuc dau \ cho Windows
                return tokens[0].replace('/', '\\'), tokens[1].replace('/', '\\')
        except Exception:
            pass
        # Fallback: tach tai khoang trang dau tien
        parts = text.strip().split(None, 1)
        return (parts[0], parts[1]) if len(parts) >= 2 else (parts[0], '')

    def handle_fs_commands(self, response):
        """
        Quet phan hoi tim cac lenh file-system dac biet va thuc thi chung.
        Tra ve ket qua tong hop (hoac chuoi rong neu khong co lenh nao).
        """
        results = []

        fs_commands = [
            'list_dir', 'read_file', 'write_file',
            'append_file', 'delete_file', 'mkdir',
            'move_file', 'copy_file'
        ]

        for cmd in fs_commands:
            # Kiem tra nhanh truoc khi chay regex
            if f'```{cmd}' not in response:
                continue

            blocks = self._extract_blocks(response, cmd)
            for block in blocks:
                block = block.strip()
                lines = block.splitlines()
                if not lines:
                    continue

                first_line = lines[0].strip()
                rest = "\n".join(lines[1:])

                print(f"\n--- He thong thuc thi lenh: {cmd} ({first_line[:60]}) ---")

                if cmd == 'list_dir':
                    result = self.fs_list_dir(first_line)
                elif cmd == 'read_file':
                    result = self.fs_read_file(first_line)
                elif cmd == 'write_file':
                    result = self.fs_write_file(first_line, rest)
                elif cmd == 'append_file':
                    result = self.fs_append_file(first_line, rest)
                elif cmd == 'delete_file':
                    result = self.fs_delete_file(first_line)
                elif cmd == 'mkdir':
                    result = self.fs_mkdir(first_line)
                elif cmd == 'move_file':
                    src, dst = self._parse_two_paths(first_line)
                    result = self.fs_move_file(src, dst) if dst else "Thieu duong dan dich"
                elif cmd == 'copy_file':
                    src, dst = self._parse_two_paths(first_line)
                    result = self.fs_copy_file(src, dst) if dst else "Thieu duong dan dich"
                else:
                    result = "Lenh khong xac dinh"

                print(f"Ket qua: {result}")
                results.append(f"[{cmd}] {result}")

        return "\n".join(results)

    # ------------------------------------------------------------------ #
    #  Vong lap chinh                                                      #
    # ------------------------------------------------------------------ #

    def run(self):
        print("Agent da san sang. Nhap 'exit' de thoat.")

        while True:
            user_input = input("\nUser: ")
            if user_input.lower() in ['exit', 'quit']:
                print("Dang dung Agent...")
                break

            if not user_input.strip():
                continue

            # Kiem tra va cat giam lich su truoc khi gui
            self.trim_history()

            self.history.append({"role": "user", "content": user_input})

            response = self.call_api(self.history)
            print(f"\nAgent: {response}")
            self.history.append({"role": "assistant", "content": response})

            max_retries = 3
            current_retry = 0

            while current_retry < max_retries:
                has_python = "```python" in response
                fs_result = self.handle_fs_commands(response)

                if not has_python and not fs_result:
                    break  # Khong con lenh nao can thuc thi

                combined_result = ""

                # Chay code Python neu co
                if has_python:
                    try:
                        py_segments = response.split("```python")
                        if len(py_segments) > 1:
                            code_block = py_segments[1].split("```")[0].strip()
                            exec_result = self.execute_python(code_block)
                            print(f"\nKet qua he thong: {exec_result}")
                            combined_result += exec_result
                    except Exception as e:
                        print(f"Loi boc tach code: {e}")

                # Them ket qua lenh file neu co
                if fs_result:
                    combined_result += ("\n" if combined_result else "") + fs_result

                if combined_result:
                    self.history.append({"role": "system", "content": combined_result})
                    response = self.call_api(self.history)
                    print(f"\nAgent (Phan tich ket qua): {response}")
                    self.history.append({"role": "assistant", "content": response})

                    # Ket thuc neu khong con lenh
                    if "```python" not in response and not any(
                        f"```{c}" in response for c in [
                            'list_dir','read_file','write_file',
                            'append_file','delete_file','mkdir',
                            'move_file','copy_file'
                        ]
                    ):
                        break
                else:
                    break

                current_retry += 1

            self.save_memory()


if __name__ == "__main__":
    agent = RAGAgent()
    try:
        agent.run()
    except KeyboardInterrupt:
        print("\nDa ngat chuong trinh.")