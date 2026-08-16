"""启动 FastAPI 服务。

用法：python scripts/run-server.py [--port 8000]
"""
import argparse
import sys
import uvicorn
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    uvicorn.run("app.api:app", host=args.host, port=args.port, reload=False)
