"""报告结构完整性校验（对照 SPEC 2.3 统一 JSON schema）：
检查 report 的 summary/risks/clauses/meta 结构、risk 字段齐全性、
legalBasis 三档合法性、法条引用带 ID/版本、findings 状态机字段。

用法：python scripts/check_report_schema.py [path/to/report.json]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REQUIRED_RISK_FIELDS = {
    "clauseId", "clauseQuote", "riskType", "severity", "legalBasis",
    "evidence", "suggestion", "suggestionClauseText", "status", "disputed",
}
VALID_SEVERITY = {"high", "medium", "low"}
VALID_TIER = {"direct", "indirect", "none"}
VALID_STATUS = {"proposed", "upheld", "rejected", "disputed"}
FORBIDDEN_LAW = ("1999 年合同法", "合同法（1999", "《中华人民共和国合同法》")


def check_report(report: dict) -> list[str]:
    problems = []
    # 顶层结构
    for key in ("contract", "summary", "risks", "clauses", "meta"):
        if key not in report:
            problems.append(f"缺顶层字段 {key}")
    s = report.get("summary", {})
    for key in ("high", "medium", "low", "total"):
        if key not in s:
            problems.append(f"summary 缺 {key}")
    # risks
    for i, r in enumerate(report.get("risks", [])):
        missing = REQUIRED_RISK_FIELDS - set(r.keys())
        if missing:
            problems.append(f"risk[{i}] 缺字段 {missing}")
        if r.get("severity") not in VALID_SEVERITY:
            problems.append(f"risk[{i}] 非法 severity {r.get('severity')}")
        if r.get("status") not in VALID_STATUS:
            problems.append(f"risk[{i}] 非法 status {r.get('status')}")
        lb = r.get("legalBasis", {})
        if lb.get("tier") not in VALID_TIER:
            problems.append(f"risk[{i}] 非法 tier {lb.get('tier')}")
        if lb.get("tier") == "direct" and not (lb.get("articleId") and lb.get("version")):
            problems.append(f"risk[{i}] direct 档缺 articleId/version（可溯源硬约束）")
        if lb.get("tier") == "none" and lb.get("articleId"):
            problems.append(f"risk[{i}] none 档不应带 articleId")
        for bad in FORBIDDEN_LAW:
            if bad in json.dumps(r, ensure_ascii=False):
                problems.append(f"risk[{i}] 含旧合同法字样")
    # reviewLog（复核模块级）
    for i, item in enumerate(report.get("meta", {}).get("reviewLog", [])):
        if item.get("verdict") not in VALID_STATUS:
            problems.append(f"reviewLog[{i}] 非法 verdict {item.get('verdict')}")
    return problems


def main() -> None:
    targets = []
    if len(sys.argv) > 1:
        targets.append(Path(sys.argv[1]))
    else:
        # 检查 eval/output 全部真实报告 + demo 缓存
        for p in sorted(Path("eval/output").rglob("*.json")):
            targets.append(p)
        for p in sorted(Path("frontend/public/reports").glob("*.json")):
            targets.append(p)
    total = 0
    for p in targets:
        if p.name == "eval-results.json":
            continue
        try:
            report = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ✗ {p}: JSON 解析失败 {e}")
            continue
        problems = check_report(report)
        total += 1
        if problems:
            print(f"  ✗ {p}: {len(problems)} 个问题")
            for pr in problems[:4]:
                print(f"      - {pr}")
        else:
            print(f"  ✓ {p}")
    print(f"\n[check_report_schema] {total} 份报告检查完成")


if __name__ == "__main__":
    main()
