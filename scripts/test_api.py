"""FastAPI 集成测试（需服务已启动：python scripts/run-server.py）。"""
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else '8011'}"


def main() -> None:
    h = httpx.get(f"{BASE}/health", timeout=10)
    print("health:", h.json())
    demo = Path("eval/dataset/demo/demo_high.txt").read_text(encoding="utf-8")
    r = httpx.post(
        f"{BASE}/upload",
        data={"text": demo, "contract_type": "purchase"},
        timeout=30,
    )
    print("upload:", r.json())
    tid = r.json()["taskId"]
    for _ in range(60):
        rep = httpx.get(f"{BASE}/report/{tid}", timeout=10).json()
        if rep["status"] != "running":
            break
        time.sleep(0.5)
    print("status:", rep["status"])
    if rep["status"] == "done":
        s = rep["report"]["summary"]
        print("summary:", s)
        print("mock:", rep["report"]["meta"]["mock"])
    else:
        print(rep)
    assert rep["status"] == "done", "pipeline failed"
    print("[API 集成测试 PASS]")


if __name__ == "__main__":
    main()
