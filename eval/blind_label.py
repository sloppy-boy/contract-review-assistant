"""盲标交叉验证（SPEC 2.6）：硅基流动 Qwen（不同家族，防共偏）盲标植入缺陷。

用途限定：只用于评估金标准集的标注质量（抽查植入记录是否合理、是否漏植），
绝不用于修正 worker 输出——它是标注质检，不是推理环节。

用法：python eval/blind_label.py --split dev [--limit 5]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import LABEL_MODEL, SILICONFLOW_API_KEY, SILICONFLOW_BASE_URL  # noqa: E402
from eval.score import normalize_clause_id  # noqa: E402

BLIND_SYSTEM = (
    "你是独立合同风险标注员（盲标，不参考任何既有标注）。"
    "阅读合同全文，输出严格 JSON：{\"defects\": [{\"clauseId\": \"条款号\", "
    "\"riskType\": \"风险类型\", \"severity\": \"high|medium|low\"}]}。"
    "只标你认为确实存在的风险条款，宁缺毋滥。"
)


def blind_label_one(contract_text: str) -> dict | None:
    """Qwen 盲标单份合同。无 key/失败返回 None。"""
    if not SILICONFLOW_API_KEY:
        return None
    import httpx

    with httpx.Client(timeout=180) as client:
        resp = client.post(
            f"{SILICONFLOW_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": LABEL_MODEL,
                "messages": [
                    {"role": "system", "content": BLIND_SYSTEM},
                    {"role": "user", "content": contract_text[:6000]},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
                "max_tokens": 2000,
                "thinking": {"type": "disabled"},  # Qwen3 系列默认 thinking，盲标质检无需深度思考
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def _semantic_match(g: tuple, p: tuple) -> bool:
    """盲标质检匹配：条款号一致（规范化）且风险语义重叠
    （riskType 共享 ≥1 个二字以上关键词，或 severity 一致）——盲标用自然语言描述，
    严格三元组逐字匹配对质检过严；质检目标是"是否识别出该条款的风险"。"""
    if normalize_clause_id(g[0]) != normalize_clause_id(p[0]):
        return False
    gt, pt = g[1], p[1]
    if gt == pt:
        return True
    gk = {gt[i : i + 2] for i in range(len(gt) - 1) if gt[i : i + 2].strip()}
    pk = {pt[i : i + 2] for i in range(len(pt) - 1) if pt[i : i + 2].strip()}
    if gk & pk:
        return True
    return g[2] == p[2]  # 同条款同严重度兜底


def compare(blind: dict, label: dict) -> dict:
    """盲标与植入记录对比（标注质量质检：一致率/漏植率/额外发现）。"""
    gold = {(normalize_clause_id(d["clauseId"]), d["riskType"], d["severity"]) for d in label.get("defects", [])}
    pred = {
        (normalize_clause_id(d.get("clauseId", "")), d.get("riskType", ""), d.get("severity", ""))
        for d in blind.get("defects", [])
    }
    hit = {g for g in gold if any(_semantic_match(g, p) for p in pred)}
    missed = gold - hit
    extra = pred - {p for p in pred if any(_semantic_match(g, p) for g in gold)}
    return {
        "contractId": label["contractId"],
        "gold": len(gold), "blind": len(pred),
        "agreed": len(hit), "missed": len(missed), "extra": len(extra),
        "missedDetail": sorted(missed, key=str)[:5],
        "extraDetail": sorted(extra, key=str)[:5],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()
    if not SILICONFLOW_API_KEY:
        print("[skip] 无 SILICONFLOW_API_KEY（盲标需硅基流动 Qwen）")
        return
    dataset = Path("eval/dataset") / args.split
    labels = sorted((dataset / "labels").glob("*.json"))
    implants = [l for l in labels if json.loads(l.read_text(encoding="utf-8"))["group"] == "implant"]
    results = []
    for lp in implants[: args.limit]:
        label = json.loads(lp.read_text(encoding="utf-8"))
        text = (dataset / "contracts" / f"{label['contractId']}.txt").read_text(encoding="utf-8")
        blind = blind_label_one(text)
        if blind is None:
            continue
        r = compare(blind, label)
        results.append(r)
        print(f"  {r['contractId']}: 金标准 {r['gold']} 盲标 {r['blind']} "
              f"一致 {r['agreed']} 漏 {r['missed']} 多 {r['extra']}")
    if results:
        n = len(results)
        agreed = sum(r["agreed"] for r in results)
        gold = sum(r["gold"] for r in results)
        print(f"\n盲标质检（{n} 份）：与植入记录一致率 {agreed/gold:.1%}"
              f"（仅评估标注质量；绝不用于修正 worker 输出）")


if __name__ == "__main__":
    main()
