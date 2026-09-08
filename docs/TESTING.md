# 测试资料（TESTING）

> 合同审查助手 · 测试体系与全部测试资产汇总。所有评测数字真实跑出于 held-out test（DeepSeek API）。

## 合同资产与履约测试（2026-09-07 新增）

当前后端全量 **312 passed**；前端 **24 passed**，生产构建通过。测试全部使用合成文档、临时数据库和注入式向量编码器，不调用模型、不保留合同原件。

| 文件 | 验证行为 |
|---|---|
| `tests/test_document_import.py` | 23 项：TXT/MD/DOCX/PDF、正文顺序与位置、显式元数据来源、损坏/体积/页数、扫描与混合 PDF、OCR 超时和错误脱敏 |
| `tests/test_asset_store.py` | 14 项：持久化、私有/租户/共享 ACL、原件、正文检索和引用问答、乐观版本、义务、撤权与提醒隔离、软删除与清理 |
| `tests/test_asset_semantic.py` | 21 项：真实余弦排序、输入/向量边界、本地模型配置、禁止联网下载和路径缓存 |
| `tests/test_asset_api.py`、`test_asset_reminders.py` | 9 项：批量部分失败、认证/成员、全读取端点权限、提醒去重及静态资源路径不冲突 |
| `frontend/tests/asset-api.test.js` | 3 项：文件字节、更新/义务 revision、检索参数及 HTTP 冲突 |
| `scripts/check_collaboration_ui.py --assets` | 浏览器导入 → 引用问答 → 履约事项 → 站内提醒 → 送入审查工作台 |

红绿记录见 [合同资产实施计划](superpowers/plans/2026-09-07-contract-assets.md)，配置和使用边界见 [合同资产文档](CONTRACT_ASSETS.md)。

## 审查协作测试（2026-09-07 新增）

协作相关测试已纳入后端全量结果；前端 `npm --prefix frontend test` **24 passed**，`npm --prefix frontend run build` 通过（仍有既有大 chunk 和依赖 PURE 注释提示）。

| 文件 | 验证行为 |
|---|---|
| `tests/test_collaboration.py` | 28 项：状态机、权限、租户、SLA、并发版本、持久化、多轮冻结快照、评论及通知隔离、模型重校验 |
| `tests/test_collaboration_api.py` | 12 项：完整认证协作流程、无效关联、角色/成员、版本冲突、退回再审、通知已读及失败无副作用 |
| `frontend/tests/collaboration-api.test.js` | 6 项：真实本地 HTTP 传输、请求体、查询、审批和错误传播 |
| `frontend/tests/collaboration-view.test.js` | 2 项：执行真实组件脚本，延迟回复保持新事项评论草稿、旧工作区响应不能恢复旧身份 |
| `scripts/check_collaboration_ui.py` | 临时合成数据库上的浏览器创建、提交、指派/SLA、关联报告、评论、冻结依据与批准 |

前端测试不需要模型密钥；浏览器脚本依赖 agent-browser CLI，先运行生产构建。新增功能使用红绿循环，记录见 [协作实施计划](superpowers/plans/2026-09-07-review-collaboration.md)。

## Playbook 基础测试（2026-09-07 新增）

以下新增测试全部使用合成输入，SQLite 文件位于临时目录；流水线强制 mock。历史章节的测试数量是此前基线，当前数量以 `python -m pytest tests/ -q` 为准。

| 文件 | 验证行为 |
|---|---|
| `tests/test_playbooks.py` | 领域约束、草稿/发布/归档、并发版本号、适用范围、租户隔离、快照内容及身份完整性 |
| `tests/test_playbook_runs.py` | 快照持久化、旧库迁移、输入/输出副本、失败和历史保留、报告防覆盖 |
| `tests/test_playbook_api.py` | 生命周期接口、认证权限、上传选择、排队期间规则更新、字段规范化 |
| `tests/test_playbook_pipeline.py` | A/B/C 全 mock 流水线传递、独立副本、非法快照拒绝和旧调用兼容 |
| `tests/test_playbook_defaults.py` | 默认基线覆盖现有矩阵、稳定标识和矩阵变更后的新标识 |

红绿过程见 [实施记录](superpowers/plans/2026-09-07-playbook-foundation.md)，接口和本次边界见 [Playbook 文档](PLAYBOOKS.md)。

## 本地治理与检索门禁（2026-09-08 新增）

| 文件 | 验证行为 |
|---|---|
| `tests/test_task_queue.py` | SQLite 任务领取、幂等确认、退避重试、死信、取消和过期 worker 恢复 |
| `tests/test_data_lifecycle.py` | 租户隔离的法律保全、删除审计、保全排除和 dry-run 清理 |
| `tests/test_core.py`、`tests/test_asset_api.py` | 审查取消/死信、运行保留期清理、资产软删除、保全阻断和管理员硬清理 |
| `tests/test_industrial.py` | 稠密/关键词合并 rerank、Recall@K、MRR、引用准确率门禁 |
| `scripts/evaluate_retrieval.py` | 仅使用查询与结果 ID 的离线检索门禁，失败返回退出码 1 |
| `tests/test_backup.py`、`scripts/backup_data.py` | 数据库 allowlist、在线备份、SHA-256 校验和原子恢复 |
| `tests/test_workflow_automation.py` | 出站重试、失败告警标记、幂等投递记录 |
| `scripts/stress_local_queue.py` | 临时数据库上的并发领取压力检查 |

操作边界见 [本地治理与评测](PLATFORM_GOVERNANCE.md)。

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
python -m pytest tests/ -q   # 当前全量结果见本文顶部
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
