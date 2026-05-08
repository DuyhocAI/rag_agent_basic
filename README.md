# RAG Agent v5.0

Agent AI chạy local với bộ nhớ hai lớp (STM + LTM), có thể thực thi code Python, chạy lệnh shell, điều khiển chuột/bàn phím, và tự kiểm tra kết quả qua Supervisor model.

---

## Kiến trúc

```
cli.py          ←  giao diện terminal (REPL + dashboard)
server.py       ←  FastAPI server, agent engine, supervisor loop
UI.py           ←  rich-based rendering (panels, progress bar, tables)
config.json     ←  cấu hình model, port, API key
agent_memory.db ←  SQLite long-term memory (tự tạo khi chạy lần đầu)
```

**Memory:**
- **STM** (Short-Term Memory) — RAM, sliding window các turn hội thoại gần nhất
- **LTM** (Long-Term Memory) — SQLite, lưu episodes, facts, procedures giữa các phiên

**Supervisor loop:** Sau mỗi lần agent trả lời, Supervisor model đánh giá kết quả và đặt ra expected output. Phiên chỉ kết thúc khi Supervisor xác nhận `score = 1.0` — hoặc khi user ấn `Ctrl+C`.

---

## Yêu cầu

- Python 3.10+
- Windows (đường dẫn mặc định dùng `D:\rag_agent`; xem mục [Đổi đường dẫn](#đổi-đường-dẫn) nếu dùng Linux/macOS)
- API key từ [ckey.vn](https://ckey.vn) (hoặc OpenAI-compatible provider khác)

---

## Cài đặt

### 1. Clone repo

```bash
git clone https://github.com/your-username/rag_agent.git
cd rag_agent
```

### 2. Cài thư viện

```bash
pip install -r requirements.txt
```

Nếu muốn dùng tính năng điều khiển chuột/bàn phím (mouse, keyboard, screenshot):

```bash
pip install pyautogui pillow
```

### 3. Cấu hình

Sao chép và chỉnh `config.json`:

```bash
cp config.example.json config.json
```

Điền các trường sau vào `config.json`:

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "ckey_api_key": "sk-...",
  "model": "claude-opus-4-6",
  "supervisor_model": "gpt-5.5",
  "openai_base_url": "https://ckey.vn/v1"
}
```

| Trường | Mô tả |
|---|---|
| `ckey_api_key` | API key từ ckey.vn (bắt buộc) |
| `model` | Model dùng cho agent (xem danh sách bên dưới) |
| `supervisor_model` | Model dùng cho supervisor |
| `openai_base_url` | Base URL của provider. Mặc định `https://ckey.vn/v1` |
| `server.host` | `0.0.0.0` để bind tất cả interface (server), `127.0.0.1` nếu chỉ local |
| `server.port` | Port của server, mặc định `8080` |

**Models có sẵn:**

```
Agent:      claude-opus-4-6 | claude-sonnet-4-6 | gpt-5.5 | gpt-5.4
Supervisor: gpt-5.5 | gpt-5.4 | claude-opus-4-6 | claude-sonnet-4-6
```

---

## Đổi đường dẫn

Mặc định project dùng `D:\rag_agent` (Windows). Cần đổi ở **2 chỗ**:

### `server.py` — dòng 72

```python
# Trước
BASE_DIR = Path(r"D:\rag_agent")

# Sau (ví dụ Linux/macOS)
BASE_DIR = Path("/home/yourname/rag_agent")

# Sau (ví dụ Windows ổ C)
BASE_DIR = Path(r"C:\Users\yourname\rag_agent")
```

`BASE_DIR` kiểm soát:
- Nơi lưu `server.log`
- Nơi lưu `agent_memory.db` (SQLite LTM)
- Thư mục làm việc mặc định của agent (`_DEFAULT_CWD`)
- Thư mục lưu projects khi dùng Mode 2 (`/projects/`)

### `cli.py` — dòng 143

```python
# Trước
BASE_DIR = Path(r"D:\rag_agent")

# Sau — đổi giống hệt server.py
BASE_DIR = Path("/home/yourname/rag_agent")
```

`BASE_DIR` trong `cli.py` kiểm soát nơi lưu `cli.log`.

> Sau khi đổi, thư mục sẽ tự tạo khi chạy lần đầu — không cần tạo tay.

---

## Chạy

### Bước 1 — Khởi động server

```bash
python server.py
```

Server khởi động tại `http://0.0.0.0:8080`. Để chạy nền:

```bash
# Windows
start /B python server.py

# Linux/macOS
nohup python server.py &
```

### Bước 2 — Mở CLI

```bash
python cli.py
```

Chọn từ menu:
- `1` — Chatbot mode (chat thường, agent thực thi code/action)
- `2` — Project mode (generate → debate → test → self-fix pipeline)
- `3` — Server status
- `4` — Long-term memory
- `5` — Server logs

Hoặc truy cập thẳng:

```bash
python cli.py chat           # vào chatbot ngay
python cli.py run "task..."  # chạy project một lần
python cli.py status         # xem status
```

---

## Lệnh trong Chatbot mode

### Hội thoại thường

Gõ bất kỳ — agent sẽ trả lời, thực thi code/action nếu cần, và Supervisor tự đánh giá đến khi đạt yêu cầu.

```
you [rag_agent]> liệt kê tất cả folder trong D:\
you [rag_agent]> viết script Python đọc file CSV và vẽ biểu đồ
you [rag_agent]> chụp màn hình rồi lưu vào D:\screenshot.png
```

### Slash commands

| Lệnh | Mô tả |
|---|---|
| `/cwd [path]` | Xem hoặc đổi thư mục làm việc |
| `/ls [path]` | Liệt kê thư mục |
| `/read <file>` | Đọc nội dung file |
| `/shell <cmd>` | Chạy lệnh shell |
| `/reset` | Xóa session (STM → LTM trước khi xóa) |
| `/history` | Xem conversation window hiện tại |
| `/execlog` | Xem lịch sử thực thi code |
| `/memory` | Snapshot đầy đủ STM + LTM |
| `/memory facts [cat]` | Liệt kê facts trong LTM |
| `/memory recall <query>` | Tìm kiếm LTM |
| `/remember key=value \| cat` | Lưu fact vào LTM |
| `/screenshot [path]` | Chụp màn hình |
| `/mouse move x y` | Di chuyển chuột |
| `/mouse click x y` | Click chuột |
| `/key ctrl+s` | Nhấn phím tắt |
| `/type text` | Gõ văn bản |
| `/model` | Xem/đổi model |
| `/model supervisor <tên>` | Đổi supervisor model |
| `/session <id>` | Chuyển session |
| `/help` | Xem tất cả lệnh |
| `/exit` | Thoát |

### Internet commands

| Lệnh | Mô tả |
|---|---|
| `/search <query>` | Tìm kiếm DuckDuckGo |
| `/fetch <url>` | Đọc nội dung trang web |
| `/wiki <query>` | Tóm tắt Wikipedia |
| `/weather <city>` | Thời tiết hiện tại |
| `/download <url> [-> path]` | Tải file về máy |

---

## Cấu trúc thư mục sau khi chạy

```
rag_agent/
├── server.py
├── cli.py
├── UI.py
├── config.json
├── requirements.txt
├── README.md
├── server.log          ← tự tạo
├── cli.log             ← tự tạo
├── agent_memory.db     ← SQLite LTM, tự tạo
└── projects/           ← output của Project mode
    └── task_xxx/
        ├── main.py
        └── test_main.py
```

---

## requirements.txt

```
fastapi
uvicorn[standard]
openai
pydantic
rich
```

Tùy chọn (mouse/keyboard control):

```
pyautogui
pillow
```

---

## Biến môi trường

Thay cho `ckey_api_key` trong config.json, có thể dùng biến môi trường:

```bash
# Linux/macOS
export OPENAI_API_KEY=sk-...

# Windows CMD
set OPENAI_API_KEY=sk-...

# Windows PowerShell
$env:OPENAI_API_KEY="sk-..."
```

---

## Lưu ý bảo mật

- **Không commit `config.json`** — file này chứa API key. Thêm vào `.gitignore`:

```
config.json
agent_memory.db
*.log
*.bak.*
projects/
```

- Thêm `config.example.json` (đã xóa key) để người khác biết cấu trúc.

---

## Troubleshooting

**Server không khởi động được:**
```bash
# Kiểm tra port có bị dùng chưa
netstat -ano | findstr :8080   # Windows
lsof -i :8080                  # Linux/macOS
```

**CLI báo "Server not responding":**
- Đảm bảo `python server.py` đang chạy
- Kiểm tra `host` và `port` trong `config.json` khớp giữa server và client

**Lỗi `pyautogui`:**
```bash
pip install pyautogui pillow
# Linux cần thêm:
sudo apt install python3-tk python3-dev
```

**Agent không thực hiện action (liệt kê thư mục, v.v.):**
- Supervisor sẽ tự nudge agent. Không cần làm gì — chờ đến khi Supervisor xác nhận xong
- Nếu muốn dừng giữa chừng: `Ctrl+C`
