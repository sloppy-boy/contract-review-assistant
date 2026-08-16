"""LangGraph 流水线（SPEC 2.3）：上传合同 → 抽取 → workers 并行扇出 → 复核 → 报告。

- 复核节点可插拔：REVIEW_MODE=A（无复核）时 workers 直接进报告；B/C 走复核。
- 打回重证分支可开关：B 直滤 / C 打回（一次迭代），由复核节点内部协作完成。
- 并发隔离（SPEC 2.9）：FastAPI 每个请求必须新建 graph/state 实例（黑板是内存态，
  共享实例会串数据）——本模块 build_graph() 每次调用返回全新图。
- mock/规则模式（无 key）：全链路规则降级（抽取正则/workers=规则基线/复核放行），
  仅用于链路自检，评测数字必须真实 key 跑出。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .config import REVIEW_MODE, VERIFY_MODE
from .legal.retrieval import HybridRetriever
from .llm import LLMClient
from .nodes.extract import build_extract_node
from .nodes.report import report_node
from .nodes.reviewer import build_reviewer_node
from .nodes.workers import build_worker_node, worker_fanout_categories
from .state import ContractState


def build_graph(
    llm: LLMClient | None = None,
    retriever: HybridRetriever | None = None,
    review_mode: str | None = None,
    verify: bool | None = None,
) -> StateGraph:
    """构造（并编译）LangGraph 流水线。每次调用返回全新实例（并发隔离）。"""
    mode = (review_mode or REVIEW_MODE).upper()
    do_verify = VERIFY_MODE if verify is None else verify
    if retriever is None:
        retriever = HybridRetriever()

    builder = StateGraph(ContractState)
    cats = worker_fanout_categories()

    builder.add_node("extract", build_extract_node(llm))
    for c in cats:
        builder.add_node(f"worker_{c}", build_worker_node(c, retriever, llm))
    builder.add_node("reviewer", build_reviewer_node(retriever, llm, mode=mode, verify=do_verify))
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
) -> dict:
    """便捷入口：单合同跑流水线，返回报告 dict（统一 JSON schema）。

    每个调用新建 graph/state 实例（并发隔离）。llm 客户端按需创建并释放；
    真实模式下在 report.meta 补 tokens 与成本（元，按实际 usage 计费）。
    """
    llm = LLMClient() if not _no_key() else None
    import time as _time

    t0 = _time.time()
    try:
        graph = build_graph(llm=llm, review_mode=review_mode, verify=verify)
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
        final = graph.invoke(initial)
        report = final.get("report", {})
        # 复核模块级口径所需过程记录（score.py 三组口径之②）
        review_results = final.get("review_results", [])
        if review_results:
            report.setdefault("meta", {})["reviewLog"] = [r.model_dump() for r in review_results]
        # 全流水线耗时（覆盖 report_node 内局部计时，score 延迟口径用）
        report.setdefault("meta", {})["latencyMs"] = int((_time.time() - t0) * 1000)
        if llm is not None:
            report.setdefault("meta", {})["tokens"] = {
                "input": llm.total_input_tokens,
                "output": llm.total_output_tokens,
            }
            report["meta"]["costYuan"] = round(llm.cost_yuan(), 4)
        return report
    finally:
        if llm is not None:
            llm.close()


def _no_key() -> bool:
    from .config import DEEPSEEK_API_KEY

    return not DEEPSEEK_API_KEY
