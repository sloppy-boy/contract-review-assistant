# 测试资料（TESTING）

> 合同审查助手 · 测试体系与全部测试资产汇总。所有评测数字真实跑出于 held-out test（DeepSeek API）。

---

## 一、测试体系总览（四层）

| 层级 | 内容 | 位置 | 规模 |
|---|---|---|---|
| 单元测试 | 黑板 reducer / 抽取 / 规则基线 / 计分 / 预算裁剪 | `tests/test_core.py` | 18 个，全部通过 |
| 链路自检 | mock 三档流水线（A/B/C） | `scripts/smoke_test.py` | PASS |
| 结构校验 | 报告 schema / 法条 / 离线缓存真实性 | `scripts/check_report_schema.py`、`verify_manual.py`、`check_demo_cache.py` | 210+3 份全过 |
| 评测 | 金标准集 + score.py 严格口径 + 消融 + 盲标 | `eval/` | dev 43 + test 19 |

---

## 二、单元测试（tests/test_core.py · 18 个全过）

```bash
python -m pytest tests/ -q   # 18 passed
```

| 分组 | 覆盖点 |
|---|---|
| **findings reducer** | 同条款+同类型+同严重度去重（保留 evidence 更充分者）；A 档无复核跨 worker 撞车退化合并；复核按 id 状态流转（proposed→disputed）；不同三元组不误合并 |
| **抽取** | 中文条款/数字条款分块；**空白条款保留**（缺失型缺陷不丢）；关键数值提取（paymentDays 取最大天数） |
| **规则基线** | 违约金>30%→high；定金>20%；仲裁无机构；**干净条款零误报** |
| **计分** | 条款号规范化（第三条→3、第十二条→12）；严格三元组命中；部分分（仅条款对=0.4）；**防重复计数**（模型重复输出只计 1 次命中） |
| **预算裁剪** | worker 输入 ≤8K tokens（12000 字符裁剪） |

## 三、链路与集成测试

| 测试 | 命令 | 结果 |
|---|---|---|
| mock 三档流水线自检 | `python scripts/smoke_test.py` | A/B/C 均跑通（强制 mock，不烧 token） |
| schema 校验降级 | 坏 JSON → 重试 → 跳过，不崩链 | ✅（单测验证） |
| 真实 LLM 小样本 | `python scripts/run-eval.py --split test --modes C --limit 2 --jobs 2` | ✅ 2/2 成功 |
| 报告结构校验（210 份） | `python scripts/check_report_schema.py` | ✅ 全过（字段齐全/direct 档带 ID+版本/无旧合同法/状态机合法） |
| API 集成 | `python scripts/test_api.py 8000` | ✅ /health /upload /report |
| 前端 | `npm run build`（frontend/） | ✅ 构建成功 |
| 离线缓存真实性 | `python scripts/check_demo_cache.py` | ✅ 3/3 为真实 pipeline 导出（mock=false） |
| 法条硬检查 | `python scripts/verify_manual.py --check` | ✅ 无旧合同法、无空条文 |
| 数据集可复现 | 两次 `make_dataset.py --force` 对比 hash | ✅ 一致 |

---

## 四、评测资料（金标准集 + 报告 + 指标）

### 4.1 金标准集（eval/dataset/）

| 目录 | 内容 |
|---|---|
| `dev/` | 43 份合同（植入 21 / 干净 14 / 边界 8）+ 43 份标签（标准答案）——调参集 |
| `test/` | 19 份合同（植入 9 / 干净 6 / 边界 4）+ 19 份标签——**held-out 最终汇报，只碰一次** |
| `demo/` | 3 份演出合同 + 3 份标签（前端一键载入） |
| `标注指南.md` | 风险矩阵 13 类 + 严重度 rubric + 标注规则（与 app/legal/risk_matrix.py 同一锚） |
| `meta.json` | 生成计划 / 缺陷池 / 变体占比 78% / **人工抽样复核 11 份（17.7%）** |

每份标签示例（标准答案 = 植入记录）：
```json
{ "contractId": "dev_implant_00", "group": "implant",
  "defects": [{ "clauseId": "第五条", "riskType": "违约金过高", "severity": "high", "defectId": "d_penalty_40" }] }
```

### 4.2 评测报告（eval/output/，210 份 JSON）

```
eval/output/{A,B,C,baseline}/{dev,test}/{contractId}.json
```
- C 档 62 份（dev 43 + test 19）、baseline 62 份、A/B 各 43 份（dev 消融）
- 报告结构：contract / summary（分级汇总+分布）/ risks（按严重度排序，含法条三档+evidence+示范条款）/ clauses（全条款导航）/ meta（tokens+成本+延迟+reviewLog）

### 4.3 指标（score.py 严格口径：(条款,类型,严重度) 逐字段一致才命中）

**held-out test（最终汇报）**：

| 指标（C 档） | 数值 | 及格线 |
|---|---|---|
| 植入缺陷组召回率 | **91.7%** | ≥85% ✅ |
| 干净组误报率（误报密度） | **1.4%** | ≤15% ✅ |
| 赢规则基线 | 召回 +50.0 / F1 +17.0 点 | ≥+10 ✅ |
| 成本 | **0.053 元/份** | <1 元 ✅ |
| 延迟（全流水线） | **17.6s/份** | <60s ✅ |

**消融实验（dev，复核价值量化）**：

| 档位 | 召回 | 精确 | F1 | 干净组误报率 |
|---|---|---|---|---|
| A 无复核 | 100.0% | 33.8% | 50.6% | 16.7% ❌ |
| B 复核直滤 | 86.4% | **67.9%** | **76.0%** | **5.4%** ✅ |
| **C 复核+打回** | **86.4%** | 65.5% | 74.5% | 8.3% ✅ |
| 规则基线 | 22.7% | 100% | 37.0% | 0% |

- 复核模块级（C 档 test）：滤掉真误报 47、误杀真阳性 1、打回重证改判正确率 97.9%
- 盲标交叉验证（Qwen3.5，不同家族）：与植入记录一致率 100%、零漏植

### 4.4 前端评测页数据

`frontend/public/eval-results.json` —— dev/test 双口径 A/B/C/baseline 全指标（前端"评测对比"页消费）

---

## 五、复现命令（一条龙）

```bash
pip install -r requirements.txt

# 1. 生成金标准集（可复现，含 dev/test/demo/标注指南/人工抽查记录）
python eval/make_dataset.py --force

# 2. 单元测试
python -m pytest tests/ -q

# 3. mock 链路自检（不烧 token）
python scripts/smoke_test.py

# 4. 真实评测（需要 .env 配置 DEEPSEEK_API_KEY / SILICONFLOW_API_KEY）
python scripts/run-eval.py --split test --modes C,baseline --jobs 4
python eval/score.py --reports eval/output --split test --modes C,baseline

# 5. 消融（dev 全量 A/B/C/baseline）
python scripts/run-eval.py --split dev --modes A,B,C,baseline --jobs 4
python eval/score.py --reports eval/output --split dev --modes A,B,C,baseline

# 6. 导出前端评测页 + 离线演示缓存
python scripts/export_eval_summary.py
python scripts/export_demo.py C

# 7. 结构/法条/缓存校验
python scripts/check_report_schema.py
python scripts/verify_manual.py --check
python scripts/check_demo_cache.py
```

---

## 六、测试资产清单（文件路径）

| 资产 | 路径 |
|---|---|
| 单元测试 | `tests/test_core.py` |
| 金标准集 | `eval/dataset/`（dev/ test/ demo/ 标注指南.md meta.json） |
| 评测报告（210 份） | `eval/output/` |
| 评测汇总（前端用） | `frontend/public/eval-results.json` |
| 离线演示缓存 | `frontend/public/reports/`（3 份真实导出） |
| 测试/工具脚本 | `scripts/`（smoke_test / run-eval / score 入口 / check_report_schema / verify_manual / check_demo_cache / test_api / export_eval_summary / export_demo / inventory / **analyze_missed / check_coverage / smoke_real**） |
| 测试报告（本文档） | `docs/TESTING.md` |
