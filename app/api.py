"""FastAPI 后端（SPEC 2.8）：/upload /report /health /export/word，服务同一流水线。

- **并发隔离（必须）**：每个请求新建 graph/state 实例（黑板是内存态，共享实例会串数据）。
- 任务异步执行（进程内线程池 + 任务表，SPEC 不做数据库）。
- 离线演示缓存：真实 pipeline 跑出后导出（scripts/export_demo.py），严禁手工编报告。
"""
from __future__ import annotations

import os
import threading
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .config import DEEPSEEK_MODEL, DEEPSEEK_REVIEWER_MODEL, using_mock
from .export_word import report_to_docx
from .graph import run_pipeline

app = FastAPI(title="合同审查助手", version="0.1.0")

TASKS: dict[str, dict] = {}  # 进程内任务表（不做数据库）

# 后端并发限制：同时最多运行 N 条流水线（防多用户并发打爆 LLM API 限流）
PIPELINE_MAX_CONCURRENT = int(os.environ.get("PIPELINE_MAX_CONCURRENT", "2"))
_pipeline_semaphore = threading.Semaphore(PIPELINE_MAX_CONCURRENT)
# 任务表内存上限：清理最旧的已完成任务，防无限增长
TASKS_MAX = int(os.environ.get("TASKS_MAX", "200"))


def _trim_tasks() -> None:
    """超过上限时清理最旧的 done/failed 任务（running 保留）。"""
    if len(TASKS) <= TASKS_MAX:
        return
    finished = [k for k, v in TASKS.items() if v["status"] in ("done", "failed")]
    for k in sorted(finished, key=lambda k: TASKS[k].get("startedAt", 0))[: len(TASKS) - TASKS_MAX]:
        TASKS.pop(k, None)


class ExportWordReq(BaseModel):
    report: dict


@app.post("/export/word")
def export_word(req: ExportWordReq) -> Response:
    """报告 JSON → Word 文档（python-docx）。离线/在线报告均可导出（无状态）。"""
    try:
        docx_bytes = report_to_docx(req.report)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Word 导出失败：{e}")
    name = (req.report.get("contract", {}).get("name") or "contract-review-report")[:40]
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{name}.docx"'},
    )


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mock": using_mock(),
        "model": DEEPSEEK_MODEL,
        "reviewerModel": DEEPSEEK_REVIEWER_MODEL,
    }


@app.post("/upload")
async def upload(
    file: UploadFile | None = None,
    text: str | None = Form(default=None),
    contract_type: str = Form(default="purchase"),
) -> dict:
    """上传合同（文件或文本）→ 返回 taskId（异步跑流水线）。"""
    if file is not None:
        content = (await file.read()).decode("utf-8", errors="ignore")
    elif text:
        content = text
    else:
        raise HTTPException(status_code=400, detail="需提供文件或文本")
    if not content.strip():
        raise HTTPException(status_code=400, detail="合同内容为空")
    task_id = uuid.uuid4().hex
    TASKS[task_id] = {"status": "running", "startedAt": time.time()}
    _trim_tasks()  # 防任务表无限增长
    threading.Thread(target=_run, args=(task_id, content, contract_type), daemon=True).start()
    return {"taskId": task_id, "status": "running"}

@app.get("/report/{task_id}")
def report(task_id: str) -> dict:
    """轮询任务结果（报告 JSON）。"""
    task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


def _run(task_id: str, content: str, contract_type: str) -> None:
    with _pipeline_semaphore:  # 排队执行（超出并发上限的任务等待，不拒绝）
        try:
            start = time.time()
            r = run_pipeline(content, contract_type=contract_type, contract_name=task_id)
            TASKS[task_id] = {
                "status": "done",
                "report": r,
                "elapsedMs": int((time.time() - start) * 1000),
            }
        except Exception as e:  # 部分成功原则：单任务失败不崩服务
            TASKS[task_id] = {"status": "failed", "error": str(e)}
