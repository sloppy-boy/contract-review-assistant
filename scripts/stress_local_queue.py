"""Small deterministic queue contention check; uses a temporary database only."""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.task_queue import TaskQueue


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise concurrent local queue claims")
    parser.add_argument("--tasks", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.tasks <= 5000 or not 1 <= args.workers <= 64:
        parser.error("tasks must be 1..5000 and workers must be 1..64")
    with tempfile.TemporaryDirectory(prefix="cra-queue-stress-") as directory:
        queue = TaskQueue(Path(directory) / "tasks.db")
        for index in range(args.tasks):
            task_id = f"stress-{index}"
            queue.enqueue(task_id, kind="stress", tenant_id="stress", payload={"requestId": task_id})

        def drain(worker_index: int):
            count = 0
            while queue.claim(f"worker-{worker_index}", tenant_id="stress") is not None:
                count += 1
            return count

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            claimed = sum(pool.map(drain, range(args.workers)))
        elapsed = max(time.perf_counter() - started, 1e-9)
        print({"tasks": args.tasks, "workers": args.workers, "claimed": claimed, "seconds": round(elapsed, 4), "claimsPerSecond": round(claimed / elapsed, 2)})
        return 0 if claimed == args.tasks else 1


if __name__ == "__main__":
    raise SystemExit(main())
