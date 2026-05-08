from pathlib import Path
import subprocess
import sys

root = Path(r"D:\rag_agent")
out_path = root / "__pytest_cli_auto_verified.txt"

cmd = [
    sys.executable,
    "-m",
    "pytest",
    r"agent_tests\test_cli_auto.py",
    "-vv",
    "--tb=long",
]

proc = subprocess.run(
    cmd,
    cwd=root,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
)

out_path.write_text(proc.stdout, encoding="utf-8", errors="replace")

lines = proc.stdout.splitlines()
print(f"RETURN_CODE={proc.returncode}")
print(f"TOTAL_LINES={len(lines)}")
print("===FULL_OUTPUT_START===")
for i, line in enumerate(lines, 1):
    print(f"{i:03}: {line}")
print("===FULL_OUTPUT_END===")

print("===SUMMARY_HINTS===")
for line in lines:
    low = line.lower()
    if (
        "failed" in low
        or "error" in low
        or "passed" in low
        or "collected" in low
        or "short test summary info" in low
    ):
        print(line)