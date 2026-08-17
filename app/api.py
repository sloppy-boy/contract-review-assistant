"""FastAPI 后端（SPEC 2.8）：/upload /report /health /export/word /balance，服务同一流水线。

- **并发隔离（必须）**：每个请求新建 graph/state 实例（黑板是内存态，共享实例会串数据）。
- 任务异步执行（进程内线程池 + 任务表，SPEC 不做数据库）。
- 离线演示缓存：真实 pipeline 跑出后导出（scripts/export_demo.py），严禁手工编报告。
- 线程安全：TASKS 任务表全部读写经 _tasks_lock（S2，防并发迭代崩溃）。
- 余额预警：/balance 探活账户余额（前端横幅 + 余额耗尽时阻止使用并提示停止服务）。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from urllib.parse import quote

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .config import (
    BALANCE_WARN_THRESHOLD,
    DEEPSEEK_MODEL,
    DEEPSEEK_REVIEWER_MODEL,
    MAX_UPLOAD_CHARS,
    using_mock,
)
from .export_word import report_to_docx
from .graph import run_pipeline
from .llm import BalanceError
from .settings_store import active_llm_config, load_settings, public_settings, save_settings

logger = logging.getLogger(__name__)

app = FastAPI(title="合同审查助手", version="0.1.0")

TASKS: dict[str, dict] = {}  # 进程内任务表（不做数据库）
_tasks_lock = threading.Lock()  # TASKS 全量读写锁（S2：dict 多线程读写无锁会抛迭代崩溃）

# 后端并发限制：同时最多运行 N 条流水线（防多用户并发打爆 LLM API 限流）
PIPELINE_MAX_CONCURRENT = int(os.environ.get("PIPELINE_MAX_CONCURRENT", "2"))
_pipeline_semaphore = threading.Semaphore(PIPELINE_MAX_CONCURRENT)
# 任务表内存上限：清理最旧的已完成任务，防无限增长
TASKS_MAX = int(os.environ.get("TASKS_MAX", "200"))

# 文件名消毒（S1）：HTTP 头禁止控制字符/引号/反斜杠（防头注入）；换行/CR 亦属控制字符
_FILENAME_UNSAFE_RE = re.compile(r'[\x00-\x1f\x7f"\\/:*?<>|]')


def _sanitize_filename(name: str, fallback: str = "contract-review-report") -> str:
    """消毒用户提交的合同名 → 安全的文件名主干（不含扩展名）。"""
    cleaned = _FILENAME_UNSAFE_RE.sub("_", str(name or "")).strip(" .")
    return cleaned[:60] or fallback


def _trim_tasks() -> None:
    """超过上限时清理最旧的 done/failed 任务（running 保留）。调用方须持 _tasks_lock。"""
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
    except Exception:  # S17：不回显内部异常细节（信息泄露）；具体原因记服务端日志
        logger.exception("Word 导出失败")
        raise HTTPException(status_code=500, detail="Word 导出失败：报告数据不合法")
    name = _sanitize_filename(req.report.get("contract", {}).get("name"))
    # S1：filename 仅 ASCII 安全字符；中文经 RFC 5987 filename* 传递（latin-1 500 根除）
    ascii_name = name.encode("ascii", "ignore").decode("ascii").strip(" .") or "contract-review-report"
    filename = f"{name}.docx"
    disposition = (
        f'attachment; filename="{ascii_name}.docx"; filename*=UTF-8\'\'{quote(filename, safe="")}'
    )
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": disposition},
    )


@app.get("/health")
def health() -> dict:
    """健康检查：mock 状态 + 当前生效的模型路由（主链路/复核，均从运行时设置解析）。"""
    main_cfg = active_llm_config("main")
    review_cfg = active_llm_config("review")
    return {
        "status": "ok",
        "mock": using_mock(),
        "model": main_cfg["model"] if main_cfg else DEEPSEEK_MODEL,
        "modelProvider": main_cfg["provider"] if main_cfg else "deepseek",
        "reviewerModel": review_cfg["model"] if review_cfg else DEEPSEEK_REVIEWER_MODEL,
        "reviewerProvider": review_cfg["provider"] if review_cfg else "deepseek",
    }


@app.get("/balance")
def balance() -> dict:
    """账户余额探活（前端预警横幅 / 余额耗尽阻止使用）。

    - 仅对 DeepSeek 官方端点查询余额（/user/balance 为标准接口）；
      其他供应商（opencode-go 等订阅制）无余额接口 → available=None（前端不提示）。
    - mock 模式（无 key / DSH_FORCE_MOCK）→ available=None（前端不提示）。
    - available=False / balance<=0：前端提示"API 供应商停止服务"并阻止提交。
    """
    cfg = active_llm_config("main")
    is_deepseek = bool(cfg and "api.deepseek.com" in (cfg["baseUrl"] or ""))
    if using_mock() or not cfg or not cfg["apiKey"] or not is_deepseek:
        note = "" if (using_mock() or not cfg or not cfg["apiKey"]) else "当前供应商为订阅制，无余额查询接口"
        return {
            "available": None,
            "balance": None,
            "threshold": BALANCE_WARN_THRESHOLD,
            "mock": using_mock() or not cfg or not cfg["apiKey"],
            "note": note,
        }
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{cfg['baseUrl']}/user/balance",
                headers={"Authorization": f"Bearer {cfg['apiKey']}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        infos = data.get("balance_infos") or []
        # 只统计人民币账户（DeepSeek 主账户为 CNY）；多账户求和
        total = sum(
            float(i.get("total_balance") or 0)
            for i in infos
            if str(i.get("currency", "")).upper() == "CNY"
        )
        return {
            "available": bool(data.get("is_available")),  # 账户可用性（余额耗尽时为 False）
            "balance": round(total, 2),
            "threshold": BALANCE_WARN_THRESHOLD,
            "mock": False,
        }
    except Exception as e:
        # 余额查询失败：不阻塞使用，前端显示"无法查询余额"
        logger.warning("余额查询失败：%s", e)
        return {
            "available": None,
            "balance": None,
            "threshold": BALANCE_WARN_THRESHOLD,
            "mock": False,
            "error": "余额查询失败（可能为网络/供应商接口波动）",
        }


# ================================================================ 设置 / 供应商管理
@app.get("/settings")
def get_settings() -> dict:
    """设置页数据（脱敏）：providers（baseUrl/hasKey/models/价格）+ 模型路由选择。"""
    return public_settings()


@app.get("/providers/{pid}/models")
def provider_models(pid: str) -> dict:
    """实时拉取指定供应商的模型列表（OpenAI 兼容 /models；失败回退本地预置目录）。"""
    cfg = load_settings()
    prov = (cfg.get("providers") or {}).get(pid)
    if not prov or not prov.get("apiKey") or not prov.get("baseUrl"):
        raise HTTPException(status_code=400, detail=f"供应商 {pid} 未配置（缺 baseUrl/apiKey）")
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{prov['baseUrl'].rstrip('/')}/models",
                headers={"Authorization": f"Bearer {prov['apiKey']}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        if ids:
            return {"provider": pid, "models": ids, "source": "live"}
        # 空列表：回退本地预置
        return {"provider": pid, "models": prov.get("models", []), "source": "fallback"}
    except Exception as e:
        logger.warning("拉取 %s 模型列表失败：%s", pid, e)
        return {"provider": pid, "models": prov.get("models", []), "source": "fallback", "error": str(e)[:120]}


class ProviderTestReq(BaseModel):
    """测试连接请求：用设置中的 key 发起最小 chat 调用验证连通性。"""

    model: str


@app.post("/providers/{pid}/test")
def provider_test(pid: str, req: ProviderTestReq) -> dict:
    """测试供应商连通性：最小 chat 调用（max_tokens=8），验证 key + 模型可用。"""
    cfg = load_settings()
    prov = (cfg.get("providers") or {}).get(pid)
    if not prov or not prov.get("apiKey") or not prov.get("baseUrl"):
        raise HTTPException(status_code=400, detail=f"供应商 {pid} 未配置（缺 baseUrl/apiKey）")
    if not req.model:
        raise HTTPException(status_code=400, detail="请先选择要测试的模型")
    try:
        with httpx.Client(timeout=40) as client:
            resp = client.post(
                f"{prov['baseUrl'].rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {prov['apiKey']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": req.model,
                    "messages": [{"role": "user", "content": "回复OK"}],
                    "max_tokens": 16,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        usage = data.get("usage") or {}
        return {"ok": True, "provider": pid, "model": req.model, "usage": {
            "in": usage.get("prompt_tokens", 0), "out": usage.get("completion_tokens", 0),
        }}
    except Exception as e:
        return {"ok": False, "provider": pid, "model": req.model, "error": str(e)[:200]}


class SettingsUpdateReq(BaseModel):
    """设置保存请求：providers / mainModel / reviewModel / common（apiKey 空 = 保留原值）。"""

    providers: dict = {}
    mainModel: dict | None = None
    reviewModel: dict | None = None
    common: dict | None = None


@app.put("/settings")
def put_settings(req: SettingsUpdateReq) -> dict:
    """保存设置（写 settings.json）。模型路由下次审查即时生效；common 类重启后端生效。"""
    saved = save_settings(
        {
            "providers": req.providers,
            "mainModel": req.mainModel,
            "reviewModel": req.reviewModel,
            "common": req.common,
        }
    )
    return public_settings()


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
    if len(content) > MAX_UPLOAD_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"合同内容过长（{len(content)} 字符，上限 {MAX_UPLOAD_CHARS}），请拆分后上传",
        )
    task_id = uuid.uuid4().hex
    with _tasks_lock:
        TASKS[task_id] = {
            "status": "running",
            "startedAt": time.time(),
            "stage": 0,
            "stageStatus": "idle",          # idle → running（进入阶段）→ done（阶段完成）
            "stageTimes": [0, 0, 0, 0],     # 各阶段耗时 ms（与 STAGES 一一对应）
            "stageDetail": "",
        }
        _trim_tasks()  # 防任务表无限增长
    threading.Thread(target=_run, args=(task_id, content, contract_type), daemon=True).start()
    return {"taskId": task_id, "status": "running"}


@app.get("/report/{task_id}")
def report(task_id: str) -> dict:
    """轮询任务结果（报告 JSON）。"""
    with _tasks_lock:
        task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


def _run(task_id: str, content: str, contract_type: str) -> None:
    with _pipeline_semaphore:  # 排队执行（超出并发上限的任务等待，不拒绝）
        try:
            start = time.time()
            start_ms = time.time() * 1000

            def _progress(stage: int, status: str, detail: str = "") -> None:
                """流水线真实进度 → TASKS（前端轮询渲染：阶段 + 各阶段耗时）。"""
                now_ms = time.time() * 1000
                with _tasks_lock:
                    t = TASKS.get(task_id)
                    if t is None or t.get("status") != "running":
                        return
                    if status == "running":
                        # 进入新阶段（或同阶段继续）→ 记录开始时间；同阶段 worker 计数更新 detail
                        if t.get("stage") != stage or t.get("stageStatus") != "running":
                            t["stage"] = stage
                            t["stageStatus"] = "running"
                            t["stageStartedAt"] = now_ms
                        t["stageDetail"] = detail
                    else:  # done：结算该阶段耗时（秒回阶段保底展示 1.5s，前端轮询才可见）
                        started = t.get("stageStartedAt") or now_ms
                        times = t.setdefault("stageTimes", [0, 0, 0, 0])
                        elapsed = int(now_ms - started)
                        if elapsed < 1500 and stage > 0:
                            # 0 findings 等场景复核/报告生成秒回：补齐展示窗口，
                            # 否则前端 1s 轮询采样不到该阶段（UI 显示"跳变"）
                            time.sleep((1500 - elapsed) / 1000)
                            now_ms = time.time() * 1000
                            elapsed = int(now_ms - started)
                        times[stage] = elapsed
                        t["stage"] = stage
                        t["stageStatus"] = "done"
                        t["stageDetail"] = detail

            r = run_pipeline(
                content,
                contract_type=contract_type,
                contract_name=task_id,
                progress=_progress,
            )
            with _tasks_lock:
                TASKS[task_id] = {
                    "status": "done",
                    "report": r,
                    "elapsedMs": int((time.time() - start) * 1000),
                    "stage": 3,
                    "stageStatus": "done",
                    "stageTimes": TASKS.get(task_id, {}).get("stageTimes", [0, 0, 0, 0]),
                }
        except BalanceError as e:  # 余额耗尽：显式标记，前端弹"停止服务"提示（不出现空报告跳转）
            logger.error("任务 %s 因 API 余额不足失败", task_id)
            with _tasks_lock:
                TASKS[task_id] = {
                    "status": "failed",
                    "error": f"API 供应商停止服务：{e}",
                    "balanceExhausted": True,
                }
        except Exception as e:  # 部分成功原则：单任务失败不崩服务
            logger.exception("流水线任务 %s 失败", task_id)
            with _tasks_lock:
                TASKS[task_id] = {"status": "failed", "error": str(e)}
