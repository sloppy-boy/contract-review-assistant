"""风险矩阵：全系统唯一锚（SPEC 2.6 / PROMPT 评测第 0 条）。

三源解耦（防"背答案"）：
- 植入缺陷生成：按本矩阵 rubric 定严重度（与标注指南同一套，保证标准答案内部自洽）
- worker 判定：LLM 用本矩阵 checklist 做通用法律审查（绝非植入记录反推）
- 规则基线：本矩阵中"确定性可代码化"的朴素子集（eval/rule_baseline.py 引用，
  只抓最直接模式，否则基线本身就是答案，worker 永远赢不了）

worker 分类必须与矩阵一一对应（矩阵是分类唯一来源，不得自行增删分类）；
违约金等子类型归入"违约责任"类目，不单列。
"""
from __future__ import annotations

from dataclasses import dataclass, field

SEVERITIES = ("high", "medium", "low")


@dataclass(frozen=True)
class Rubric:
    condition: str          # 客观阈值描述（供植入标注与人工复核统一校准）
    severity: str           # high | medium | low


@dataclass(frozen=True)
class RiskCategory:
    id: str
    name: str                       # 中文类目名
    sub_types: tuple[str, ...]      # 子类型（风险类型枚举来源）
    rubric: tuple[Rubric, ...]      # 严重度校准锚
    checklist: tuple[str, ...]      # 通用法律审查 checklist（worker 判定锚）


RISK_CATEGORIES: list[RiskCategory] = [
    RiskCategory(
        id="payment_invoice",
        name="付款/开票",
        sub_types=("付款期限过长", "逾期付款违约金缺失", "预付款比例过高", "付款条件不明确", "发票开具义务缺失"),
        rubric=(
            Rubric("付款期限 > 90 天且无逾期付款违约金/利息条款", "medium"),
            Rubric("付款期限 > 180 天或预付款比例 > 80% 且无担保安排", "high"),
            Rubric("付款条件仅依赖单方验收确认，无客观时限", "medium"),
        ),
        checklist=(
            "付款期限是否过长且缺乏对价？",
            "逾期付款是否有违约金或利息条款？比例是否失衡？",
            "预付款比例是否过高且无担保/退款机制？",
            "付款条件是否客观可判定（避免'以甲方确认为准'式单方条件）？",
            "发票开具时间/类型/税号约定是否明确？",
        ),
    ),
    RiskCategory(
        id="acceptance_delivery",
        name="验收交付",
        sub_types=("验收标准缺失或模糊", "验收期限过短", "验收不通过的后果不明", "交付期限及迟延责任缺失"),
        rubric=(
            Rubric("验收标准仅'按国家/行业标准'而无具体指标，或验收期限 < 5 个工作日", "medium"),
            Rubric("验收以对方单方/无期限确认为准，或验收不通过无任何救济", "high"),
            Rubric("交付期限缺失或迟延交付无违约金", "medium"),
        ),
        checklist=(
            "验收标准是否具体可执行（指标、方法、依据）？",
            "验收期限是否合理（考虑标的物性质）？",
            "验收不通过后的返工/更换/退货流程与时限是否明确？",
            "交付时间、地点、方式及迟延责任是否明确？",
        ),
    ),
    RiskCategory(
        id="ip",
        name="知识产权",
        sub_types=("知识产权归属不清", "交付物侵权担保缺失", "使用范围限制不明确"),
        rubric=(
            Rubric("定制/委托开发成果的知识产权归属未约定", "high"),
            Rubric("无第三方知识产权侵权担保与赔偿条款", "medium"),
        ),
        checklist=(
            "定制成果/软件的 IP 归属、许可范围是否明确？",
            "是否有不侵权担保与侵权赔偿条款？",
            "背景 IP 与前景 IP 是否区分？",
        ),
    ),
    RiskCategory(
        id="data_compliance",
        name="数据合规",
        sub_types=("个人信息处理无合法依据", "数据处理范围超出必要", "数据安全责任缺失"),
        rubric=(
            Rubric("涉及个人信息处理但无合法依据/授权条款", "high"),
            Rubric("数据共享范围、留存期限、删除义务未约定", "medium"),
        ),
        checklist=(
            "是否涉及个人信息/重要数据？处理依据是否合法（授权/合同必要）？",
            "数据范围是否最小必要？留存期限与删除义务是否明确？",
            "数据泄露通知与责任分担是否约定？",
        ),
    ),
    RiskCategory(
        id="breach_liability",
        name="违约责任",
        sub_types=("违约金过高", "违约金过低", "责任上限条款缺失", "违约救济不完整", "定金条款违规"),
        rubric=(
            Rubric("违约金比例 > 30% 合同额（明显过高，民法典 585 可调减）", "high"),
            Rubric("违约金比例 20%~30% 或与损失预估明显不匹配", "medium"),
            Rubric("违约金过低（< 10%）且无赔偿损失兜底，违约成本趋零", "medium"),
            Rubric("定金比例 > 主合同标的额 20%（超出部分不产生定金效力）", "medium"),
        ),
        checklist=(
            "违约金是否过高/过低？是否与可预见损失匹配（民法典 584/585）？",
            "定金比例是否超过标的额 20%（民法典 586）？",
            "违约金与定金是否并用（民法典 588 只能择一主张）？",
            "责任上限（如累计 ≤ 合同额）是否剥夺基本救济？",
            "违约救济是否完整（继续履行/赔偿/解除）？",
        ),
    ),
    RiskCategory(
        id="termination",
        name="解除权",
        sub_types=("解除权行使期限过短", "单方解除条件失衡", "解除后果不明"),
        rubric=(
            Rubric("解除权仅归一方且条件宽泛（如'甲方可随时解除'）", "high"),
            Rubric("解除权行使期限过短或解除后果（已付款/存货处理）未约定", "medium"),
        ),
        checklist=(
            "单方解除权是否失衡？触发条件是否客观？",
            "解除权行使期限是否过短（民法典 564 一年/合理期限）？",
            "解除后果（恢复原状/赔偿/已履行部分结算）是否明确（民法典 566）？",
        ),
    ),
    RiskCategory(
        id="confidentiality",
        name="保密",
        sub_types=("保密范围不清", "保密期限缺失", "违约后果缺失"),
        rubric=(
            Rubric("涉及商业秘密但无保密条款，或保密期限/违约责任缺失", "medium"),
        ),
        checklist=(
            "保密信息范围、期限、例外情形是否界定（反法第 9 条）？",
            "保密违约责任（违约金/赔偿）是否明确？",
            "合同解除后保密义务是否存续？",
        ),
    ),
    RiskCategory(
        id="non_compete",
        name="竞业限制",
        sub_types=("竞业限制范围过宽", "竞业限制无期限/补偿", "排他条款失衡"),
        rubric=(
            Rubric("竞业限制期限、地域、范围无边界（如'全国全行业永久'）", "high"),
            Rubric("排他供应条款无期限或与采购量不对价", "medium"),
        ),
        checklist=(
            "竞业/排他条款的范围、期限、地域是否合理有界？",
            "是否过度限制对方正常经营（可能构成显失公平）？",
            "有无对价/补偿安排？",
        ),
    ),
    RiskCategory(
        id="jurisdiction",
        name="管辖/仲裁",
        sub_types=("管辖约定违反专属管辖", "只约定仲裁未约定仲裁机构/地点", "管辖约定不明确或无效"),
        rubric=(
            Rubric("约定仲裁但未约定仲裁机构或仲裁地（仲裁协议无效风险）", "medium"),
            Rubric("管辖约定违反专属管辖或选择与争议无实际联系地点", "high"),
            Rubric("管辖条款措辞含糊（'均可'/'或'导致选择不明）", "medium"),
        ),
        checklist=(
            "协议管辖是否符合民诉法第 35 条（书面、与争议有实际联系、不违级别/专属管辖）？",
            "仲裁条款是否明确仲裁机构（如'中国国际经济贸易仲裁委员会'）与仲裁地？",
            "是否同时出现诉讼与仲裁冲突表述？",
        ),
    ),
    RiskCategory(
        id="notice",
        name="通知送达",
        sub_types=("送达地址条款缺失", "送达方式/生效时间不明"),
        rubric=(
            Rubric("无送达地址条款或仅约定一种不可靠方式（如仅邮件）", "low"),
        ),
        checklist=(
            "双方送达地址、联系人、方式是否明确？",
            "送达生效时间（签收/发出）与变更通知义务是否约定？",
        ),
    ),
    RiskCategory(
        id="force_majeure",
        name="不可抗力",
        sub_types=("不可抗力范围过宽", "不可抗力免责滥用", "通知义务缺失"),
        rubric=(
            Rubric("不可抗力范围扩张至一方经营风险/恶意行为（规避违约责任）", "high"),
            Rubric("不可抗力条款无通知义务与证明时限（民法典 590）", "medium"),
        ),
        checklist=(
            "不可抗力定义是否符合'不能预见、不能避免且不能克服'（民法典 590）？",
            "是否把市场价格波动/经营亏损等商业风险混入不可抗力？",
            "发生后通知义务、证明时限、减损义务是否约定（民法典 590/591）？",
        ),
    ),
    RiskCategory(
        id="tax",
        name="税费",
        sub_types=("税费承担约定不明", "发票与税负转移条款风险"),
        rubric=(
            Rubric("约定'一切税费由对方承担'等概括条款且税种不明", "low"),
        ),
        checklist=(
            "含税/不含税价格是否明确？发票类型与税率是否约定？",
            "税费承担条款是否与法律强制性规定冲突（如转嫁法定义务）？",
        ),
    ),
    RiskCategory(
        id="subcontract",
        name="外包分包",
        sub_types=("分包未经同意", "分包方责任转嫁不清", "总包连带责任缺失"),
        rubric=(
            Rubric("允许分包但未约定需经书面同意，或分包方违约责任未约定由总包承担", "medium"),
        ),
        checklist=(
            "分包是否需甲方书面同意？",
            "分包方履行瑕疵的责任是否明确由总包方承担（民法典 593）？",
            "分包范围是否受限（主体部分不得转包）？",
        ),
    ),
]

RISK_MATRIX: dict[str, RiskCategory] = {c.id: c for c in RISK_CATEGORIES}
WORKER_CATEGORIES: list[str] = [c.id for c in RISK_CATEGORIES]  # 与矩阵一一对应，唯一来源

# 风险类型 → 类目 映射（findings 用 riskType 表达子类型，worker 归属类目）
SUBTYPE_TO_CATEGORY: dict[str, str] = {
    st: c.id for c in RISK_CATEGORIES for st in c.sub_types
}
