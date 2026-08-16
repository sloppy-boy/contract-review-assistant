"""链路冒烟自检（强制 mock/规则模式，绝不调用真实 API、不烧 token）：
一份测试合同 → LangGraph 流水线 → 结构化报告 JSON。

用法：python scripts/smoke_test.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 必须在 import app 之前：强制 mock（即使 .env 有 key 也不烧 token）
os.environ["DSH_FORCE_MOCK"] = "1"

from app.graph import run_pipeline  # noqa: E402

TEST_CONTRACT = """产品购销合同

甲方（买方）：示例科技有限公司
乙方（卖方）：示例制造有限公司

第一条 标的物
甲方向乙方采购智能控制器 1000 台，单价 2000 元，总价 200 万元。

第二条 付款方式
合同签订后 7 日内，甲方支付预付款 60 万元（占总价款 30%）。
货物验收合格后 120 日内，甲方支付剩余货款 140 万元。

第三条 定金
甲方于合同签订后 3 日内向乙方支付定金 60 万元（占合同总价 30%）。

第四条 交付与验收
乙方应于 2026 年 3 月 1 日前交付全部货物至甲方指定仓库。
甲方应在收到货物后 3 个工作日内完成验收。验收标准按乙方提供的产品说明书执行。

第五条 违约责任
任何一方违约的，应向对方支付合同总价 35% 的违约金。
甲方逾期付款的，每逾期一日按未付款项的 0.05% 支付违约金。

第六条 不可抗力
因不可抗力致使合同无法履行的，双方互不承担责任。
不可抗力包括但不限于市场波动、原材料价格上涨。

第七条 争议解决
因本合同引起的争议，双方协商解决；协商不成的，提交仲裁解决。

第八条 保密
双方对本合同内容及合作中知悉的商业秘密负有保密义务。

第九条 通知送达
双方确认本合同首部所列地址为有效送达地址。

第十条 其他
本合同一式两份，自双方盖章之日起生效。
"""


def main() -> None:
    import sys as _sys

    try:
        _sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台乱码修复
    except Exception:
        pass
    for mode in ("A", "B", "C"):
        report = run_pipeline(
            TEST_CONTRACT,
            contract_type="purchase",
            contract_name="测试购销合同",
            review_mode=mode,
        )
        print(f"===== REVIEW_MODE={mode} =====")
        print(json.dumps(report, ensure_ascii=False, indent=2)[:4000])
        assert "risks" in report and "summary" in report
        print(f"summary: {report['summary']}")
    print("\n[mock 链路自检 PASS] A/B/C 三档均跑通（mock 数字无效，真实评测需 API key）")


if __name__ == "__main__":
    main()
