"""LangGraph 流水线（SPEC 2.3）：上传合同 → 抽取 → workers 并行扇出 → 复核 → 报告。

- 复核节点可插拔：REVIEW_MODE=A（无复核）时 workers 直接进报告；B/C 走复核。
- 打回重证分支可开关：B 直滤 / C 打回（一次迭代），由复核节点内部协作完成。
- 并发隔离（SPEC 2.9）：FastAPI 每个请求必须新建 graph/state 实例（黑板是内存态，
  共享实例会串数据）——本模块 build_graph() 每次调用返回全新图。
- mock/规则模式（无 key）：全链路规则降级（抽取正则/workers=规则基线/复核放行），
  仅用于链路自检，评测数字必须真实 key 跑出。
"""
from __future__ import annotations

import os
import time as _time

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .config import REVIEW_MODE, VERIFY_MODE, using_mock
from .legal.retrieval import HybridRetriever
from .llm import LLMClient
from .nodes.extract import build_extract_node
from .nodes.report import report_node
from .nodes.reviewer import build_reviewer_node
from .nodes.workers import build_worker_node, worker_fanout_categories
from .settings_store import active_llm_config
from .state import ContractState


def build_graph(
    llm: LLMClient | None = None,
    retriever: HybridRetriever | None = None,
    review_mode: str | None = None,
    verify: bool | None = None,
    reviewer_llm: LLMClient | None = None,
) -> StateGraph:
    """构造（并编译）LangGraph 流水线。每次调用返回全新实例（并发隔离）。

    reviewer_llm：复核 thinking 档客户端（deepseek-reasoner，独立单价）。
    缺省回退到主 llm（保持兼容），但 run_pipeline 总会传入独立复核客户端。
    """
    mode = (review_mode or REVIEW_MODE).upper()
    do_verify = VERIFY_MODE if verify is None else verify
    if retriever is None:
        retriever = HybridRetriever()

    builder = StateGraph(ContractState)
    cats = worker_fanout_categories()

    builder.add_node("extract", build_extract_node(llm))
    for c in cats:
        builder.add_node(f"worker_{c}", build_worker_node(c, retriever, llm))
    builder.add_node(
        "reviewer",
        build_reviewer_node(retriever, llm, reviewer=reviewer_llm, mode=mode, verify=do_verify),
    )
    builder.add_node("report_gen", lambda s: report_node(s, mode=mode))

    builder.add_edge(START, "extract")

    # 并行扇出：extract → 每个 worker（Send 扇出；Send payload 即目标节点 state 视图，
    # 故需携带 worker 所需的黑板事实）
    def fanout(state: ContractState):
        clauses = state.get("clauses", [])
        return [Send(f"worker_{c}", {"clauses": clauses}) for c in cats]

    builder.add_conditional_edges("extract", fanout, [f"worker_{c}" for c in cats])

    # 复核可插拔：A 档跳过复核直接报告；B/C 档经复核
    def after_workers(state: ContractState):
        return "report_gen" if mode == "A" else "reviewer"

    for c in cats:
        builder.add_conditional_edges(
            f"worker_{c}", after_workers, {"report_gen": "report_gen", "reviewer": "reviewer"}
        )
    builder.add_edge("reviewer", "report_gen")
    builder.add_edge("report_gen", END)
    return builder.compile()


def run_pipeline(
    contract_text: str,
    contract_type: str = "purchase",
    contract_name: str = "",
    review_mode: str | None = None,
    verify: bool | None = None,
    progress=None,
) -> dict:
    """便捷入口：单合同跑流水线，返回报告 dict（统一 JSON schema）。

    每个调用新建 graph/state 实例（并发隔离）。llm 客户端按需创建并释放；
    真实模式下在 report.meta 补 tokens 与成本（元，按实际 usage 计费，
    复核节点走独立客户端（设置页可配 thinking 档模型），成本按各档独立单价合并）。

    模型路由（运行时设置）：主链路与复核分别从 settings_store 的
    mainModel / reviewModel 解析（供应商 baseUrl + apiKey + 模型名 + 单价）；
    任一缺失或 DSH_FORCE_MOCK=1 → 该档回退 mock（不烧 token）。

    progress: 可选回调 progress(stage, status, detail="")——
      stage 0 条款抽取 / 1 风险识别(workers) / 2 对抗复核 / 3 报告生成；
      status "running" | "done"；detail 如 "5/13"（worker 完成计数）。
      基于 LangGraph stream("updates") 的真实节点产出（前端进度与报告时间同步）。
    """
    forced_mock = os.environ.get("DSH_FORCE_MOCK", "") == "1"
    reviewer = None
    llm = None
    main_cfg = None if forced_mock else active_llm_config("main")
    if main_cfg is not None:
        llm = LLMClient(
            api_key=main_cfg["apiKey"],
            base_url=main_cfg["baseUrl"],
            model=main_cfg["model"],
            price_in=main_cfg["priceIn"],
            price_out=main_cfg["priceOut"],
        )
    # 复核档：独立配置（可不同供应商/模型/单价）；仅当主链路真实时启用
    if llm is not None:
        review_cfg = active_llm_config("review")
        if review_cfg is not None:
            reviewer = LLMClient(
                api_key=review_cfg["apiKey"],
                base_url=review_cfg["baseUrl"],
                model=review_cfg["model"],
                price_in=review_cfg["priceIn"],
                price_out=review_cfg["priceOut"],
            )
        else:
            reviewer = llm.reviewer()  # 兼容回退：复用主供应商的 reviewer 档

    t0 = _time.time()
    try:
        graph = build_graph(llm=llm, review_mode=review_mode, verify=verify, reviewer_llm=reviewer)
        initial: ContractState = {
            "contract_text": contract_text,
            "contract_type": contract_type,
            "contract_name": contract_name,
            "clauses": [],
            "findings": [],
            "review_results": [],
            "errors": [],
            "trace": [],
            "meta": {},
        }
        # 真实进度：stream("updates") 逐个节点产出（vs invoke 一次性返回）
        # 阶段事件序列必须连续（每阶段 running → done），否则前端进度条卡住：
        #   extract 开始前发 0 running；extract done 后立即发 1 running（workers 将执行）；
        #   13/13 done 后立即发 2 running（reviewer 将执行）；reviewer done 后立即发 3 running。
        total_workers = len(worker_fanout_categories())
        done_workers = 0
        mode = (review_mode or REVIEW_MODE).upper()
        report: dict = {}
        review_results: list = []
        if progress:
            progress(0, "running", "")
        for chunk in graph.stream(initial, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "extract":
                    if progress:
                        progress(0, "done", f"{len(update.get('clauses', []))} 条")
                        progress(1, "running", "0/13")  # workers 即将执行
                elif node.startswith("worker_"):
                    done_workers += 1
                    if progress:
                        progress(1, "done" if done_workers >= total_workers else "running",
                                 f"{done_workers}/{total_workers}")
                        if done_workers >= total_workers:
                            if mode == "A":
                                progress(2, "done", "已跳过（A 档无复核）")
                                progress(3, "running", "")  # report_gen 即将执行
                            else:
                                progress(2, "running", "")  # reviewer 即将执行
                elif node == "reviewer":
                    review_results = update.get("review_results", [])
                    if progress:
                        progress(2, "done", f"{len(review_results)} 条裁决")
                        progress(3, "running", "")  # report_gen 即将执行
                elif node == "report_gen":
                    report = update.get("report", {})
                    if progress:
                        progress(3, "done", "")
        # 复核模块级口径所需过程记录（score.py 三组口径之②）
        if review_results:
            report.setdefault("meta", {})["reviewLog"] = [r.model_dump() for r in review_results]
        # 全流水线耗时（覆盖 report_node 内局部计时，score 延迟口径用）
        report.setdefault("meta", {})["latencyMs"] = int((_time.time() - t0) * 1000)
        if llm is not None:
            # 主链路 + 复核档独立口径：tokens 合并展示，成本按各自单价分别计算后求和
            total_in = llm.total_input_tokens + (reviewer.total_input_tokens if reviewer else 0)
            total_out = llm.total_output_tokens + (reviewer.total_output_tokens if reviewer else 0)
            report.setdefault("meta", {})["tokens"] = {"input": total_in, "output": total_out}
            report["meta"]["costYuan"] = round(
                llm.cost_yuan() + (reviewer.cost_yuan() if reviewer else 0.0), 4
            )
        return report
    finally:
        if reviewer is not None:
            reviewer.close()
        if llm is not None:
            llm.close()


def _no_key() -> bool:
    return using_mock()
