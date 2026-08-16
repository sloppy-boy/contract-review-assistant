"""计分（SPEC 2.6/2.7）：先写计分，再跑任何指标。

- 主指标 = 风险对级（严格）：(条款, 风险类型, 严重度) 三元组逐字段完全一致才计 1 次命中
  - 召回率 = 命中数 / 植入缺陷总数；精确率 = 命中数 / 模型输出风险总数
- 部分分（辅助诊断）：条款 0.4 + 类型 0.3 + 严重度 0.3（不进简历主数字）
- 三组口径同时输出：① 系统级（最终报告）② 复核模块级（review_results）③ 规则基线
- 边界组单独报（不按严格命中）：争议识别 / 严重度倾向一致 / 承认不确定性
- 干净组误报率 = 被判定为风险的条款数 ÷ 条款总数（误报密度，无真阳性故不用精确率）
- 消融：A/B/C 三档对比 精确/召回/F1/成本/延迟；及格线以 C 档为准
- 输出 通过/未通过 标记（绿勾/红叉）

用法：python eval/score.py --reports eval/output --split dev
      python eval/score.py --reports eval/output --split test   # held-out，全程只碰一次
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import (  # noqa: E402
    BEAT_BASELINE_POINTS,
    PARTIAL_WEIGHTS,
    PASS_COST_PER_CONTRACT,
    PASS_FP_RATE,
    PASS_LATENCY_PER_CONTRACT,
    PASS_RECALL,
)

CN2AR = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
         "六": 6, "七": 7, "八": 8, "九": 9}


def normalize_clause_id(cid: str) -> str:
    """条款号规范化：'第三条'/'3'/'第 3 条' → '3'；'十二条'/'12' → '12'。"""
    s = str(cid or "").strip().replace(" ", "")
    s = s.replace("第", "").replace("条", "").strip()
    if s.isdigit():
        return s
    # 中文数字 → 阿拉伯（支持 十/十几/几十 常见形态）
    if all(ch in CN2AR or ch in "十百" for ch in s) and s:
        total, num = 0, 0
        for ch in s:
            if ch in CN2AR:
                num = CN2AR[ch]
            elif ch == "十":
                total += (num if num else 1) * 10
                num = 0
            elif ch == "百":
                total += (num if num else 1) * 100
                num = 0
        return str(total + num)
    return s


def _field_score(defect: dict, risk: dict) -> float:
    s = 0.0
    if normalize_clause_id(defect.get("clauseId")) == normalize_clause_id(risk.get("clauseId")):
        s += PARTIAL_WEIGHTS["clause"]
    if defect.get("riskType") == risk.get("riskType"):
        s += PARTIAL_WEIGHTS["riskType"]
    if defect.get("severity") == risk.get("severity"):
        s += PARTIAL_WEIGHTS["severity"]
    return s


def score_contract(label: dict, report: dict) -> dict:
    """单份合同计分：严格命中 + 部分分（贪心匹配，命中后移除，防重复计数）。"""
    defects = label.get("defects", [])
    risks = report.get("risks", [])
    matched: set[int] = set()
    hits, partial = 0, 0.0
    hit_fields = {"clause": 0, "riskType": 0, "severity": 0}
    for d in defects:
        best_i, best_s = None, 0.0
        for i, r in enumerate(risks):
            if i in matched:
                continue
            s = _field_score(d, r)
            if s > best_s:
                best_s, best_i = s, i
        if best_i is not None:
            matched.add(best_i)
            partial += best_s
            if best_s >= 0.999:  # 严格命中
                hits += 1
            for key, w in (("clause", 0.4), ("riskType", 0.3), ("severity", 0.3)):
                if best_s >= (1.0 - w + 0.001):
                    hit_fields[key] += 1
    return {
        "contractId": label.get("contractId"),
        "group": label.get("group"),
        "nDefects": len(defects),
        "nOutput": len(risks),
        "hits": hits,
        "partialScore": round(partial, 3),
        "hitFields": hit_fields,
    }


def aggregate(scores: list[dict]) -> dict:
    n_defects = sum(s["nDefects"] for s in scores)
    n_output = sum(s["nOutput"] for s in scores)
    n_hits = sum(s["hits"] for s in scores)
    recall = n_hits / n_defects if n_defects else 0.0
    precision = n_hits / n_output if n_output else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    avg_partial = sum(s["partialScore"] for s in scores) / len(scores) if scores else 0.0
    return {
        "nContracts": len(scores), "nDefects": n_defects, "nOutput": n_output,
        "hits": n_hits, "recall": recall, "precision": precision, "f1": f1,
        "avgPartial": round(avg_partial, 3),
        "hitFields": {
            k: round(sum(s["hitFields"][k] for s in scores) / n_defects, 3) if n_defects else 0.0
            for k in ("clause", "riskType", "severity")
        },
    }


def clean_fp_rate(report: dict) -> float:
    """干净组误报率（误报密度）：被判定为风险的条款数 ÷ 条款总数。"""
    n_risk_clauses = len({r.get("clauseId") for r in report.get("risks", [])})
    n_clauses = len(report.get("clauses", [])) or 1
    return n_risk_clauses / n_clauses


def review_module_metrics(labels: list[dict], reports: dict[str, dict]) -> dict:
    """复核模块级口径（SPEC 2.6 三组口径之②，A 档无复核不适用）：
    复核滤掉多少真误报 / 误杀多少真阳性 / 打回重证后改判正确率。
    判定依据：被复核驳回的 finding 三元组是否在标准答案（植入记录）中。
    """
    gold = {}
    for l in labels:
        gold[l["contractId"]] = {
            (normalize_clause_id(d.get("clauseId")), d.get("riskType"), d.get("severity"))
            for d in l.get("defects", [])
        }
    filtered_false = killed_true = 0
    reverted = upheld = reverted_correct = upheld_correct = 0
    for cid, report in reports.items():
        for item in report.get("meta", {}).get("reviewLog", []):
            if not item.get("wasRejectedByReviewer"):
                continue
            trip = (normalize_clause_id(item.get("clauseId", "")), item.get("riskType", ""), item.get("severity", ""))
            is_true = trip in gold.get(cid, set())
            if is_true:
                killed_true += 1
            else:
                filtered_false += 1
            if item.get("revertedAfterReverify"):
                reverted += 1
                if not is_true:
                    reverted_correct += 1
            if item.get("upheldAfterReverify"):
                upheld += 1
                if is_true:
                    upheld_correct += 1
    total_revert = reverted + upheld
    return {
        "filteredFalsePositives": filtered_false,   # 滤掉的真误报
        "killedTruePositives": killed_true,          # 误杀的真阳性
        "reverted": reverted, "upheld": upheld,
        "revertVerdictAccuracy": round((reverted_correct + upheld_correct) / total_revert, 3) if total_revert else None,
    }


def boundary_metrics(labels: list[dict], reports: dict[str, dict]) -> dict:
    """边界组单独报（不按严格命中）：①争议识别 ②严重度倾向一致 ③承认不确定性。"""
    n = len(labels)
    disputed_identified = 0
    tendency_ok = 0
    uncertainty_ok = 0
    for label in labels:
        cid = label["contractId"]
        report = reports.get(cid, {})
        b = label.get("boundary", {})
        risks = report.get("risks", [])
        # ① 争议识别：边界条款应被标"有争议"或至少被识别为风险
        if risks and any(r.get("disputed") for r in risks):
            disputed_identified += 1
        elif risks:
            disputed_identified += 0.5  # 识别到风险但未标争议
        # ② 严重度倾向一致性（专家倾向）
        if risks:
            top = min((r["severity"] for r in risks), key=lambda x: {"high": 0, "medium": 1, "low": 2}[x])
            if top == b.get("expert_tendency"):
                tendency_ok += 1
            elif {"high": 0, "medium": 1, "low": 2}[top] - {"high": 0, "medium": 1, "low": 2}[b.get("expert_tendency", "low")] in (-1, 1):
                tendency_ok += 0.5
        # ③ 承认不确定性（输出存在 low 档或争议标记）
        if any(r.get("severity") == "low" or r.get("disputed") for r in risks):
            uncertainty_ok += 1
    return {
        "n": n,
        "disputedIdentification": round(disputed_identified / n, 3) if n else 0.0,
        "severityTendencyAgree": round(tendency_ok / n, 3) if n else 0.0,
        "uncertaintyAcknowledged": round(uncertainty_ok / n, 3) if n else 0.0,
    }


def load_split(split_dir: Path) -> tuple[list[dict], dict[str, dict]]:
    """加载一个 split（dev/test）的标准答案与合同。"""
    labels, contracts = [], {}
    labels_dir = split_dir / "labels"
    contracts_dir = split_dir / "contracts"
    for lp in sorted(labels_dir.glob("*.json")):
        label = json.loads(lp.read_text(encoding="utf-8"))
        cid = label["contractId"]
        contracts[cid] = (contracts_dir / f"{cid}.txt").read_text(encoding="utf-8")
        labels.append(label)
    return labels, contracts


def load_reports(reports_root: Path, mode: str, split: str) -> dict[str, dict]:
    """读取某档（A/B/C/baseline）某 split 下已跑出的报告。"""
    out: dict[str, dict] = {}
    if mode == "baseline":
        base = reports_root / "baseline" / split
    else:
        base = reports_root / mode / split
    for rp in sorted(base.glob("*.json")):
        out[rp.stem] = json.loads(rp.read_text(encoding="utf-8"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", default="eval/output", help="报告根目录")
    ap.add_argument("--split", default="dev", choices=["dev", "test"], help="评测集 split")
    ap.add_argument("--dataset", default="eval/dataset", help="数据集根目录")
    ap.add_argument("--modes", default="A,B,C,baseline", help="要对比的档位（逗号分隔）")
    args = ap.parse_args()

    dataset = Path(args.dataset)
    split_dir = dataset / args.split
    labels, contracts = load_split(split_dir)
    reports_root = Path(args.reports)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    # 分组
    implants = [l for l in labels if l["group"] == "implant"]
    cleans = [l for l in labels if l["group"] == "clean"]
    boundaries = [l for l in labels if l["group"] == "boundary"]

    results: dict[str, dict] = {}
    for mode in modes:
        reports = load_reports(reports_root, mode, args.split)
        missing = [l["contractId"] for l in labels if l["contractId"] not in reports]
        if missing:
            print(f"[warn] {mode} 缺报告 {len(missing)} 份：{missing[:5]}…（先跑 scripts/run-eval.py）")
        imp_scores = [score_contract(l, reports.get(l["contractId"], {"risks": []})) for l in implants]
        agg = aggregate(imp_scores)
        fp_rates = [clean_fp_rate(reports.get(l["contractId"], {"risks": [], "clauses": []})) for l in cleans]
        agg["cleanFpRate"] = round(sum(fp_rates) / len(fp_rates), 3) if fp_rates else None
        agg["boundary"] = boundary_metrics(boundaries, reports)
        agg["reviewModule"] = review_module_metrics(implants, reports)
        agg["costPerContract"] = _cost(reports)
        agg["latencyPerContract"] = _latency(reports)
        results[mode] = agg

    _print_table(results, modes, args.split)
    _check_pass(results, args.split)


def _cost(reports: dict[str, dict]) -> float | None:
    """成本/份（元）：meta 无 token 时返回 None（mock/估算无效）。"""
    vals = [r.get("meta", {}).get("costYuan") for r in reports.values()]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _latency(reports: dict[str, dict]) -> float | None:
    """延迟/份（秒）：meta.latencyMs 为毫秒（全流水线耗时），换算为秒。"""
    vals = [r.get("meta", {}).get("latencyMs") for r in reports.values()]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals) / 1000, 1) if vals else None


def _print_table(results: dict[str, dict], modes: list[str], split: str) -> None:
    print(f"\n===== 评测结果 · split={split}（植入组，严格口径）=====")
    print(f"{'档位':<10}{'召回':>8}{'精确':>8}{'F1':>8}{'部分分':>8}{'误报率':>8}{'成本/份':>10}{'延迟/份':>10}")
    for m in modes:
        r = results[m]
        cost = f"{r['costPerContract']:.3f}元" if r["costPerContract"] is not None else "—"
        lat = f"{r['latencyPerContract']:.1f}s" if r["latencyPerContract"] is not None else "—"
        fp = f"{r['cleanFpRate']:.1%}" if r["cleanFpRate"] is not None else "—"
        print(f"{m:<10}{r['recall']:>8.1%}{r['precision']:>8.1%}{r['f1']:>8.1%}"
              f"{r['avgPartial']:>8.3f}{fp:>8}{cost:>10}{lat:>10}")
    print("\n部分分诊断（字段级命中率）：")
    for m in modes:
        hf = results[m]["hitFields"]
        print(f"  {m:<10} 条款 {hf['clause']:.1%}  类型 {hf['riskType']:.1%}  严重度 {hf['severity']:.1%}")
    print("\n边界条款组（单独报，不进主指标）：")
    for m in modes:
        b = results[m]["boundary"]
        print(f"  {m:<10} 争议识别 {b['disputedIdentification']:.1%}  倾向一致 {b['severityTendencyAgree']:.1%}  承认不确定 {b['uncertaintyAcknowledged']:.1%}")
    print("\n复核模块级（三组口径之②，A 档无复核不适用）：")
    for m in modes:
        if m == "A":
            continue
        r = results[m]["reviewModule"]
        acc = f"{r['revertVerdictAccuracy']:.1%}" if r["revertVerdictAccuracy"] is not None else "—"
        print(f"  {m:<10} 滤掉真误报 {r['filteredFalsePositives']}  误杀真阳性 {r['killedTruePositives']}"
              f"  打回({r['reverted']}撤/{r['upheld']}持) 改判正确率 {acc}")


def _check_pass(results: dict[str, dict], split: str) -> None:
    """及格线（SPEC 2.7）：C 档为准。test 集输出最终汇报标记。"""
    print(f"\n===== 及格线（C 档）=====")
    if "C" not in results:
        print("  [warn] 无 C 档数据")
        return
    c = results["C"]
    base = results.get("baseline")
    checks = []
    checks.append(("植入缺陷组召回率 ≥ 85%", c["recall"] >= PASS_RECALL, f"{c['recall']:.1%}"))
    fp = c["cleanFpRate"]
    checks.append(("干净组误报率 ≤ 15%", fp is not None and fp <= PASS_FP_RATE,
                   f"{fp:.1%}" if fp is not None else "—"))
    if base is not None:
        gain_recall = c["recall"] - base["recall"]
        gain_f1 = c["f1"] - base["f1"]
        win = (gain_recall >= BEAT_BASELINE_POINTS / 100) or (gain_f1 >= BEAT_BASELINE_POINTS / 100)
        checks.append((f"赢规则基线（召回/F1 ≥ +{BEAT_BASELINE_POINTS} 点）",
                       win, f"召回 {gain_recall:+.1%} / F1 {gain_f1:+.1%}"))
    else:
        checks.append(("赢规则基线（召回/F1 ≥ +10 点）", False, "缺基线数据"))
    cost_ok = c["costPerContract"] is not None and c["costPerContract"] < PASS_COST_PER_CONTRACT
    lat_ok = c["latencyPerContract"] is not None and c["latencyPerContract"] < PASS_LATENCY_PER_CONTRACT
    checks.append(("成本 < 1 元/份", cost_ok,
                   f"{c['costPerContract']}元" if c["costPerContract"] is not None else "mock 无 token"))
    checks.append(("延迟 < 60s/份", lat_ok,
                   f"{c['latencyPerContract']}s" if c["latencyPerContract"] is not None else "—"))
    for name, ok, val in checks:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}：{val}")
    all_pass = all(ok for _, ok, _ in checks)
    print(f"\n总体：{'✅ 通过（PASS）' if all_pass else '❌ 未通过（FAIL）'}"
          f"{' —— held-out test 最终汇报口径' if split == 'test' else ' —— dev 调参口径'}")


if __name__ == "__main__":
    main()
