# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

# Đây là chương trình Python đơn giản đầu tiên
# Mục đích: In ra dòng chữ "Hello, World!" ra màn hình

# Hàm main() là điểm bắt đầu chính của chương trình
def main():
    # Sử dụng hàm print() để hiển thị text ra console
    # "Hello, World!" là một chuỗi ký tự (string) được đặt trong dấu ngoặc kép
    print("Hello, World!")

# Kiểm tra xem file này có đang được chạy trực tiếp hay không
# __name__ == "__main__" sẽ True khi file được chạy trực tiếp
# Nếu file được import vào file khác thì điều kiện này sẽ False
if __name__ == "__main__":
    # Gọi hàm main() để thực thi chương trình
    main()