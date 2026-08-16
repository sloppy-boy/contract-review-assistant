"""金标准集生成（SPEC 2.6 / PROMPT 评测第 1 条）：可复现、三源解耦、dev/test 拆分。

- 三组：植入缺陷组（骨架 + 按风险矩阵植入缺陷，植入记录=标准答案）、干净合同组（原样）、
  边界条款组（争议条款，单独报、不进主指标）。
- 三源解耦：植入缺陷混入"规则抓不到的变体"（组合风险/跨条款推理/表述含糊）；
  规则基线保持朴素（rule_checker.py 只抓最直接确定性模式）；植入严重度按风险矩阵 rubric 定。
- dev/test：~70/30；test 用不同模板族 + 不同种子 + 不同植入组合（分布有实质差异），
  test 全程只碰一次。
- 人工抽样复核：随机抽 ~20% 合同，记录清单（README/评测页注明）。

用法：python eval/make_dataset.py [--seed 100] [--force]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.legal.risk_matrix import RISK_MATRIX  # noqa: E402

DATASET_DIR = Path(__file__).resolve().parent / "dataset"

# ================================================================ 模板族 A（dev）
FAMILY_A_TEMPLATE = """{title}

甲方（买方）：{buyer}
乙方（卖方）：{seller}

第一条 合同标的
甲方向乙方采购{product}，数量 {qty} 台，单价 {price} 元，合同总价 {total} 元。

第二条 质量标准
乙方交付的货物应符合国家标准及双方确认的技术规格书。

第三条 交付与验收
乙方应于 {delivery_date} 前将货物交付至甲方指定地点。
甲方应在收到货物后 {acceptance_days} 个工作日内完成验收，验收标准按本合同第二条约定的质量标准执行。

第四条 付款方式
合同签订后 {prepay_days} 日内，甲方向乙方支付合同总价 {prepay_pct}% 的预付款；
货物验收合格后 {pay_days} 日内，甲方支付剩余款项。

第五条 违约责任
{breach_clause}
{extra_breach}

第六条 定金
{deposit_clause}

第七条 知识产权
{ip_clause}

第八条 保密
{confidentiality_clause}

第九条 不可抗力
{force_majeure_clause}

第十条 争议解决
{dispute_clause}

第十一条 通知送达
{notice_clause}

第十二条 其他
本合同自双方盖章之日起生效，一式两份，甲乙双方各执一份。
"""

# ================================================================ 模板族 B（test）
FAMILY_B_TEMPLATE = """{title}

买受人：{buyer}
出卖人：{seller}

1. 标的物
买受人向出卖人采购{product}，数量 {qty} 台，单价 {price} 元，总价款 {total} 元。

2. 质量标准
出卖人交付的产品应当符合双方确认的技术规格以及国家强制性标准。

3. 交付与验收
出卖人应于 {delivery_date} 前将产品交付至买受人指定收货地点。
买受人应于收货后 {acceptance_days} 个工作日内完成检验，检验标准以本合同第 2 条为准。

4. 价款支付
合同订立后 {prepay_days} 日内，买受人支付总价款 {prepay_pct}% 作为预付款；
检验合格后 {pay_days} 日内，买受人支付余款。

5. 违约责任
{breach_clause}
{extra_breach}

6. 定金
{deposit_clause}

7. 知识产权
{ip_clause}

8. 保密
{confidentiality_clause}

9. 不可抗力
{force_majeure_clause}

10. 争议处理
{dispute_clause}

11. 通知与送达
{notice_clause}

12. 附则
本合同自双方签章之日起生效，一式两份，双方各执一份。
"""

# ================================================================ 默认安全 slot（干净组/骨架）
DEFAULT_SLOTS: dict[str, str] = {
    "title": "产品购销合同",
    "buyer": "示例科技有限公司",
    "seller": "示例制造有限公司",
    "product": "智能控制器",
    "qty": 1000,
    "price": 2000,
    "total": 2000000,
    "delivery_date": "2026年3月1日",
    "acceptance_days": 15,
    "prepay_days": 7,
    "prepay_pct": 30,
    "pay_days": 30,
    "breach_clause": "任何一方违约的，应向守约方支付合同总价 10% 的违约金。",
    "extra_breach": "",
    "deposit_clause": "双方约定定金为合同总价的 10%，甲方应于合同签订后 3 日内支付。",
    "ip_clause": "乙方交付的货物所含知识产权归各自权利人所有；乙方保证其交付物不侵犯任何第三方知识产权。",
    "confidentiality_clause": "双方对合同内容及合作过程中知悉的对方商业秘密负有保密义务，保密期限为合同终止后两年；违反保密义务的，应赔偿对方因此遭受的损失。",
    "force_majeure_clause": "因不可抗力不能履行合同的，根据不可抗力的影响部分或者全部免除责任，但应及时通知对方并提供证明。不可抗力指不能预见、不能避免且不能克服的客观情况。",
    "dispute_clause": "因本合同引起的争议，双方协商解决；协商不成的，向甲方所在地人民法院起诉。",
    "notice_clause": "双方确认本合同首部所列地址为有效送达地址，地址变更应提前三个工作日书面通知对方。",
}

# ================================================================ 缺陷池（按风险矩阵 rubric 定 severity）
# variant=True：规则基线抓不到（三源解耦要求的变体：组合/跨条款/缺失型/表述含糊）
# slots：要覆盖的模板槽位（可多槽，实现组合/跨条款变体）；clauseSlot：标准答案条款号对应的槽位
DEFECT_POOL: list[dict] = [
    # --- 规则基线可抓（朴素规则，worker 的对比空间）---
    {"id": "d_penalty_40", "category": "breach_liability", "riskType": "违约金过高", "severity": "high",
     "clauseSlot": "breach_clause",
     "slots": {"breach_clause": "任何一方违约的，应向守约方支付合同总价 40% 的违约金。"},
     "variant": False},
    {"id": "d_deposit_25", "category": "breach_liability", "riskType": "定金条款违规", "severity": "medium",
     "clauseSlot": "deposit_clause",
     "slots": {"deposit_clause": "双方约定定金为合同总价的 25%，甲方应于合同签订后 3 日内支付。"},
     "variant": False},
    {"id": "d_pay_120", "category": "payment_invoice", "riskType": "付款期限过长", "severity": "medium",
     "clauseSlot": "pay_days",
     "slots": {"pay_days": 120, "breach_clause": "（本合同未约定违约责任条款。）"},
     "variant": False},
    {"id": "d_arb_no_org", "category": "jurisdiction", "riskType": "只约定仲裁未约定仲裁机构/地点", "severity": "medium",
     "clauseSlot": "dispute_clause",
     "slots": {"dispute_clause": "因本合同引起的争议，双方协商解决；协商不成的，提交仲裁解决。"},
     "variant": False},
    # --- 变体（规则抓不到）---
    {"id": "d_penalty_5", "category": "breach_liability", "riskType": "违约金过低", "severity": "medium",
     "clauseSlot": "breach_clause",
     "slots": {"breach_clause": "任何一方违约的，应向守约方支付合同总价 5% 的违约金。"},
     "variant": True},
    {"id": "d_pay_waive", "category": "payment_invoice", "riskType": "逾期付款违约金缺失", "severity": "medium",
     "clauseSlot": "extra_breach",
     "slots": {"extra_breach": "甲方逾期付款的，无需承担任何逾期责任。"},
     "variant": True},
    {"id": "d_conf_missing", "category": "confidentiality", "riskType": "保密条款缺失", "severity": "medium",
     "clauseSlot": "confidentiality_clause",
     "slots": {"confidentiality_clause": "（此处空白）"}, "variant": True},
    {"id": "d_fm_market", "category": "force_majeure", "riskType": "不可抗力范围过宽", "severity": "high",
     "clauseSlot": "force_majeure_clause",
     "slots": {"force_majeure_clause": "因不可抗力致使合同无法履行的，双方互不承担责任。不可抗力包括但不限于市场波动、原材料价格上涨、政策调整、经营困难。"},
     "variant": True},
    {"id": "d_term_unilateral", "category": "termination", "riskType": "单方解除条件失衡", "severity": "high",
     "clauseSlot": "extra_breach",
     "slots": {"extra_breach": "甲方有权在任何时候单方解除本合同，无需承担任何责任。"},
     "variant": True},
    {"id": "d_accept_vague", "category": "acceptance_delivery", "riskType": "验收期限过短", "severity": "medium",
     "clauseSlot": "acceptance_days",
     "slots": {"acceptance_days": 2}, "variant": True},
    {"id": "d_ip_missing", "category": "ip", "riskType": "知识产权归属不清", "severity": "high",
     "clauseSlot": "ip_clause",
     "slots": {"ip_clause": "（此处空白）"}, "variant": True},
    {"id": "d_prepay_90", "category": "payment_invoice", "riskType": "预付款比例过高", "severity": "high",
     "clauseSlot": "prepay_pct",
     "slots": {"prepay_pct": 90}, "variant": True},
    {"id": "d_nc_unbounded", "category": "non_compete", "riskType": "竞业限制范围过宽", "severity": "high",
     "clauseSlot": "extra_breach",
     "slots": {"extra_breach": "乙方在合同终止后三年内不得与甲方的任何客户进行交易，地域不限。"},
     "variant": True},
    {"id": "d_data_nopermit", "category": "data_compliance", "riskType": "个人信息处理无合法依据", "severity": "high",
     "clauseSlot": "extra_breach",
     "slots": {"extra_breach": "乙方应向甲方提供其员工及终端用户的个人信息，供甲方进行市场分析与客户开发。"},
     "variant": True},
    {"id": "d_sub_no_consent", "category": "subcontract", "riskType": "分包未经同意", "severity": "medium",
     "clauseSlot": "extra_breach",
     "slots": {"extra_breach": "乙方可将本合同的全部义务分包给第三方履行，无需甲方同意。"},
     "variant": True},
    {"id": "d_notice_missing", "category": "notice", "riskType": "送达地址条款缺失", "severity": "low",
     "clauseSlot": "notice_clause",
     "slots": {"notice_clause": "（此处空白）"}, "variant": True},
    {"id": "d_tax_all", "category": "tax", "riskType": "税费承担约定不明", "severity": "low",
     "clauseSlot": "extra_breach",
     "slots": {"extra_breach": "本合同项下一切税费由乙方承担，包括依法应由甲方承担的税种。"},
     "variant": True},
    {"id": "d_jur_no_link", "category": "jurisdiction", "riskType": "管辖约定不明确或无效", "severity": "high",
     "clauseSlot": "dispute_clause",
     "slots": {"dispute_clause": "因本合同引起的争议，协商不成的，向西藏自治区拉萨市人民法院起诉。"},
     "variant": True},
]

# ================================================================ 边界条款组（单独报，不进主指标）
BOUNDARY_CASES: list[dict] = [
    {"id": "b_penalty_25", "desc": "违约金比例 25%（高于 20% 但低于 30%，算高算低有争议）",
     "slots": {"penalty_pct": 25}, "expert_tendency": "medium", "disputed": True},
    {"id": "b_jur_ambiguous", "desc": "管辖约定'双方可向任何一方所在地法院起诉'（选择不明）",
     "slots": {"dispute_clause": "因本合同引起的争议，协商不成的，双方可向任何一方所在地人民法院起诉。"},
     "expert_tendency": "medium", "disputed": True},
    {"id": "b_accept_5days", "desc": "验收期 5 个工作日（对复杂设备是否过短有争议）",
     "slots": {"acceptance_days": 5}, "expert_tendency": "low", "disputed": True},
    {"id": "b_pay_90", "desc": "付款期 90 天（临界值，是否有风险有争议）",
     "slots": {"pay_days": 90}, "expert_tendency": "low", "disputed": True},
    {"id": "b_deposit_20", "desc": "定金比例恰为 20%（法定上限临界，超过部分效力有争议）",
     "slots": {"deposit_clause": "双方约定定金为合同总价的 20%，甲方应于合同签订后 3 日内支付。"},
     "expert_tendency": "low", "disputed": True},
]

# ================================================================ 条款号映射（模板族 A/B）
CLAUSE_IDS_A = {  # slot → 条款号
    "acceptance_days": "第三条", "pay_days": "第四条", "prepay_pct": "第四条",
    "breach_clause": "第五条", "extra_breach": "第五条", "deposit_clause": "第六条",
    "ip_clause": "第七条", "confidentiality_clause": "第八条", "force_majeure_clause": "第九条",
    "dispute_clause": "第十条", "notice_clause": "第十一条",
}
CLAUSE_IDS_B = {  # 数字条款
    "acceptance_days": "3", "pay_days": "4", "prepay_pct": "4",
    "breach_clause": "5", "extra_breach": "5", "deposit_clause": "6",
    "ip_clause": "7", "confidentiality_clause": "8", "force_majeure_clause": "9",
    "dispute_clause": "10", "notice_clause": "11",
}

BUYERS = ["星海科技有限公司", "鼎盛智能装备有限公司", "恒远供应链管理有限公司"]
SELLERS = ["华信精密制造有限公司", "联创电子股份有限公司", "启航机电设备有限公司"]
PRODUCTS = ["工业网关", "伺服驱动器", "传感器模组"]


def _company(rng: random.Random, i: int) -> tuple[str, str]:
    return BUYERS[i % len(BUYERS)], SELLERS[(i + 1) % len(SELLERS)]


def render(family: str, slots: dict) -> str:
    s = {**DEFAULT_SLOTS, **slots}
    tpl = FAMILY_A_TEMPLATE if family == "A" else FAMILY_B_TEMPLATE
    return tpl.format(**s)


def make_implant(
    rng: random.Random, family: str, seed: int, i: int,
    forced_defects: list[dict] | None = None,
) -> tuple[str, dict]:
    """生成一份植入缺陷合同。返回 (text, label)。

    forced_defects：显式指定缺陷（dev 覆盖优先模式：保证每种缺陷至少出现一次，
    调参需要全类型反馈）；None 时随机抽样，且各缺陷模板槽位不得冲突
    （如 d_pay_waive 与 d_nc_unbounded 共用 extra_breach，同时抽到会互相覆盖）。
    """
    if forced_defects is not None:
        chosen = list(forced_defects)
    else:
        n_defects = rng.randint(1, 3)
        chosen = []
        used_slots: set[str] = set()
        pool = list(DEFECT_POOL)
        rng.shuffle(pool)
        for d in pool:
            if len(chosen) >= n_defects:
                break
            if set(d["slots"]) & used_slots:
                continue
            chosen.append(d)
            used_slots |= set(d["slots"])
    slots: dict = {}
    defects: list[dict] = []
    buyer, seller = _company(rng, i)
    slots.update({"buyer": buyer, "seller": seller, "product": PRODUCTS[i % len(PRODUCTS)]})
    cid_map = CLAUSE_IDS_A if family == "A" else CLAUSE_IDS_B
    for d in chosen:
        slots.update(d["slots"])
        defects.append(
            {"clauseId": cid_map[d["clauseSlot"]], "riskType": d["riskType"], "severity": d["severity"],
             "defectId": d["id"], "variant": d["variant"]}
        )
    return render(family, slots), {
        "contractId": f"{'dev' if family=='A' else 'test'}_implant_{i:02d}",
        "group": "implant", "family": family, "seed": seed,
        "defects": defects,
    }


def make_clean(rng: random.Random, family: str, seed: int, i: int) -> tuple[str, dict]:
    """干净合同：骨架原样（无植入，不触发 rubric）。"""
    buyer, seller = _company(rng, i)
    slots = {"buyer": buyer, "seller": seller, "product": PRODUCTS[i % len(PRODUCTS)]}
    return render(family, slots), {
        "contractId": f"{'dev' if family=='A' else 'test'}_clean_{i:02d}",
        "group": "clean", "family": family, "seed": seed, "defects": [],
    }


def make_boundary(rng: random.Random, family: str, seed: int, i: int) -> tuple[str, dict]:
    case = BOUNDARY_CASES[i % len(BOUNDARY_CASES)]
    buyer, seller = _company(rng, i)
    slots = {"buyer": buyer, "seller": seller, **case["slots"]}
    return render(family, slots), {
        "contractId": f"{'dev' if family=='A' else 'test'}_boundary_{i:02d}",
        "group": "boundary", "family": family, "seed": seed,
        "defects": [],
        "boundary": {"desc": case["desc"], "expert_tendency": case["expert_tendency"],
                     "disputed": case["disputed"]},
    }


def write_dataset(force: bool = False) -> None:
    """生成 dev/test 三组 + demo 演出合同 + 标注指南。"""
    if DATASET_DIR.exists() and any(DATASET_DIR.rglob("*.txt")) and not force:
        print(f"[skip] {DATASET_DIR} 已存在（--force 重新生成）")
        return
    # 规模（SPEC 2.6：植入 30 / 干净 20 / 边界 10~15；dev:test ≈ 7:3）
    PLAN = {
        "dev": {"family": "A", "implant": 21, "clean": 14, "boundary": 8, "seed": 100},
        "test": {"family": "B", "implant": 9, "clean": 6, "boundary": 4, "seed": 200},
    }
    audited: list[str] = []
    for split, p in PLAN.items():
        rng = random.Random(p["seed"])
        contracts_dir = DATASET_DIR / split / "contracts"
        labels_dir = DATASET_DIR / split / "labels"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        labels_dir.mkdir(parents=True, exist_ok=True)
        idx = 0
        # dev 植入组：覆盖优先（每种缺陷至少 1 次），其余随机；test 纯随机（held-out 真实分布）
        forced_rounds = 0
        if split == "dev":
            forced_rounds = len(DEFECT_POOL)
        for group, n in (("implant", p["implant"]), ("clean", p["clean"]), ("boundary", p["boundary"])):
            for i in range(n):
                forced: list[dict] | None = None
                if group == "implant" and idx < forced_rounds:
                    forced = [DEFECT_POOL[idx]]  # 每种缺陷一份
                if group == "implant":
                    text, label = make_implant(rng, p["family"], p["seed"], idx, forced_defects=forced)
                elif group == "clean":
                    text, label = make_clean(rng, p["family"], p["seed"], idx)
                else:
                    text, label = make_boundary(rng, p["family"], p["seed"], idx)
                cid = label["contractId"]
                (contracts_dir / f"{cid}.txt").write_text(text, encoding="utf-8")
                (labels_dir / f"{cid}.json").write_text(
                    json.dumps(label, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                idx += 1
        # 人工抽样复核 ~20%（记录清单，README 注明）
        all_ids = [p.stem for p in contracts_dir.glob("*.txt")]
        audited += [x for x in rng.sample(all_ids, k=max(1, int(len(all_ids) * 0.2)))]

    # demo 演出合同（高危/干净/边界各一，供前端一键载入）
    demo_dir = DATASET_DIR / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(999)
    d_text, d_label = make_implant(rng, "A", 999, 0)
    c_text, c_label = make_clean(rng, "A", 999, 1)
    b_text, b_label = make_boundary(rng, "A", 999, 2)
    for name, text, label in (("demo_high", d_text, d_label), ("demo_clean", c_text, c_label), ("demo_boundary", b_text, b_label)):
        (demo_dir / f"{name}.txt").write_text(text, encoding="utf-8")
        (demo_dir / f"{name}.json").write_text(json.dumps(label, ensure_ascii=False, indent=2), encoding="utf-8")

    # 元数据 + 标注指南
    meta = {
        "plan": PLAN,
        "defectPool": [d["id"] for d in DEFECT_POOL],
        "variantShare": sum(1 for d in DEFECT_POOL if d["variant"]) / len(DEFECT_POOL),
        "manualAudit": sorted(audited),
        "manualAuditRatio": round(len(audited) / 62, 3),
    }
    (DATASET_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATASET_DIR / "标注指南.md").write_text(_annotation_guide(), encoding="utf-8")
    print(f"[done] 数据集已生成于 {DATASET_DIR}")
    print(f"   dev: 植入21/干净14/边界8 | test: 植入9/干净6/边界4 | demo: 3")
    print(f"   缺陷池 {len(DEFECT_POOL)} 个，变体占比 {meta['variantShare']:.0%}（三源解耦）")
    print(f"   人工抽样复核 {len(audited)} 份（~20%）：{', '.join(audited[:8])}…")


def _annotation_guide() -> str:
    lines = ["# 标注指南（金标准集）", "",
             "> 本指南与 `app/legal/risk_matrix.py`（风险矩阵）为同一锚：植入缺陷的严重度、规则基线、",
             "> worker 判定、人工复核全部以风险矩阵为准（SPEC 2.6 三源解耦）。", "",
             "## 1. 风险类型枚举（唯一来源：风险矩阵，13 类）", ""]
    for cid, cat in RISK_MATRIX.items():
        lines.append(f"- **{cat.name}**（{cid}）：子类型 " + "、".join(cat.sub_types))
    lines += ["", "## 2. 严重度 rubric（植入标注据此定级）", ""]
    for cid, cat in RISK_MATRIX.items():
        for r in cat.rubric:
            lines.append(f"- {cat.name}：{r.condition} → **{r.severity}**")
    lines += ["", "## 3. 植入记录 = 标准答案", "",
              "植入缺陷组每份合同的 `labels/{id}.json` 中 `defects[]` 为唯一标准答案，",
              "三元组 `(clauseId, riskType, severity)` 逐字段完全一致才计命中。", "",
              "## 4. 人工抽样复核（~20%）", "",
              "已按 `meta.json` 的 `manualAudit` 清单随机抽查植入记录与标注指南的一致性。", "",
              "## 5. 盲标质检（Qwen 第二家族）", "",
              "`python eval/blind_label.py` 用硅基流动 Qwen 盲标植入组，仅评估标注质量",
              "（植入记录是否合理、是否漏植），绝不用于修正 worker 输出。"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="重新生成（覆盖已有数据集）")
    args = ap.parse_args()
    write_dataset(force=args.force)


if __name__ == "__main__":
    main()
