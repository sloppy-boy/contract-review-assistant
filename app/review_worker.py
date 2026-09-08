"""Queue-backed review execution orchestration."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .llm import BalanceError


def execute_review_task(
    task_id: str,
    content: str,
    contract_type: str,
    tenant_id: str,
    *,
    queue_factory: Callable,
    run_store,
    pipeline: Callable,
    semaphore,
    schedule_heartbeat: Callable,
    logger,
) -> None:
    with semaphore:
        queue = queue_factory()
        worker_id = f"review-{threading.get_ident()}"
        claimed = queue.claim(worker_id, task_id=task_id, tenant_id=tenant_id)
        if claimed is None:
            return
        schedule_heartbeat(queue, task_id, worker_id, tenant_id)

        def owns_task() -> bool:
            current = queue.get(task_id, tenant_id=tenant_id)
            return bool(current and current.get("status") == "running" and current.get("workerId") == worker_id)

        while True:
            try:
                start = time.time()
                stage_starts: dict[int, float] = {}

                def progress(stage: int, status: str, detail: str = "") -> None:
                    now_ms = time.time() * 1000
                    current = run_store.get_run(task_id)
                    if current is None or current.get("status") not in {"queued", "running"}:
                        return
                    times = current["stageTimes"]
                    if status == "running":
                        stage_starts.setdefault(stage, now_ms)
                        run_store.update_progress(task_id, stage=stage, status="running", detail=detail, stage_times=times)
                    else:
                        started = stage_starts.get(stage, now_ms)
                        times[stage] = int(now_ms - started)
                        run_store.update_progress(task_id, stage=stage, status="done", detail=detail, stage_times=times)

                bound_run = run_store.get_run(task_id)
                if bound_run is None:
                    raise KeyError(f"review run not found: {task_id}")
                if bound_run.get("status") == "cancelled":
                    queue.cancel(task_id, tenant_id=tenant_id)
                    return
                report = pipeline(
                    content,
                    contract_type=contract_type,
                    contract_name=task_id,
                    progress=progress,
                    playbook_snapshot=bound_run["playbookSnapshot"],
                )
                current = run_store.get_run(task_id) or {}
                if current.get("status") == "cancelled":
                    queue.cancel(task_id, tenant_id=tenant_id)
                    return
                if not owns_task():
                    return
                completed = run_store.complete_run(
                    task_id, report, elapsed_ms=int((time.time() - start) * 1000),
                    stage_times=current.get("stageTimes", [0, 0, 0, 0]),
                )
                if completed.get("status") == "done":
                    queue.ack(task_id, worker_id=worker_id, tenant_id=tenant_id)
                else:
                    queue.cancel(task_id, tenant_id=tenant_id)
                return
            except BalanceError as exc:
                logger.error("任务 %s 因 API 余额不足失败", task_id)
                failed = queue.fail(task_id, f"API 供应商停止服务：{exc}", worker_id=worker_id, tenant_id=tenant_id, delay_seconds=0)
                if failed is None:
                    return
                run_store.fail_run(task_id, f"API 供应商停止服务：{exc}", balance_exhausted=True)
                return
            except Exception as exc:
                logger.exception("流水线任务 %s 失败", task_id)
                try:
                    failed = queue.fail(task_id, str(exc), worker_id=worker_id, tenant_id=tenant_id, delay_seconds=0)
                except ValueError:
                    if (run_store.get_run(task_id) or {}).get("status") == "cancelled":
                        return
                    raise
                if failed is None:
                    return
                if failed and failed["status"] == "retry_wait":
                    claimed = queue.claim(worker_id, task_id=task_id, tenant_id=tenant_id)
                    if claimed is not None:
                        continue
                    if (run_store.get_run(task_id) or {}).get("status") == "cancelled":
                        return
                run_store.fail_run(task_id, str(exc))
                return
