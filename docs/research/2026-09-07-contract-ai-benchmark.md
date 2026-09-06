# 合同 AI 成熟产品能力对标（2026-09-07）

## 研究问题

`contract-review-assistant` 若定位为工业级旗舰项目，除现有的合同风险审查、多模型路由、对抗复核、规则库与审查历史外，还需要补齐哪些经过成熟市场验证的能力。

## 主要参照

| 产品 | 官方材料体现的核心能力 | 对项目的启示 |
| --- | --- | --- |
| Docusign CLM / AI Contract Review Assistant | 基于组织标准的 AI 审查、条款抽取、问答、谈判与审计轨迹 | 审查结果必须回到协作与谈判流程，而非停留在一份报告。 |
| Ironclad | 合同批量导入、AI 分类/元数据、续签管理；可治理、可审计的 AI 框架与人工监督 | 合同库、履约/续签和模型治理是企业产品的基础设施。 |
| LegalOn | Word 内审查与精准修订、带依据的建议、维护的律师标准库；事项分配和截止期限管理 | “可直接接受的红线修改”与 Playbook 才是法务日常交付物。 |

## 已具备的良好基础

- 多阶段审查链路：提取、审查、对抗复核、报告。
- 审查运行持久化、阶段耗时、失败后恢复及历史记录。
- 规则/知识库版本与快照意识，多模型角色路由。
- 基础反馈、追踪、Docker 与接口测试框架。

这些能力足以构成“可演示、可验证的审查 Agent”，但不能等同于完整 CLM 或企业级法务系统。

## 旗舰项目缺口及优先级

### P0：把审查变为可交付、可协作的法务工作

1. **审查 Playbook 工作台**：按合同类型、业务线、法域和风险偏好管理条款规则、严重度、可接受条件、批准替代条款；支持版本、审批、回归用例和生效范围。
2. **真正的红线交付**：DOCX 修订痕迹/段落级 diff、建议替换文本、证据引用、接受/驳回和理由；优先提供 Word 加载项或兼容的导出格式。
3. **审查工作流**：发起单、分派、SLA、审批关卡、评论/@提及、通知、谈判轮次和版本差异比较。
4. **审查可信度机制**：每项风险要能定位原文、规则和来源；加入置信度、拒答/升级人工策略、模型/Playbook/证据快照记录。

### P1：把单次上传扩展为合同资产管理

1. PDF/DOCX/OCR 和批量导入，自动抽取当事人、期限、金额、续签、义务等元数据。
2. 合同仓库：全文/语义检索、标签、权限、保留策略、跨合同问答和条款分析。
3. 义务、里程碑与自动续签日历，提醒、负责人和履约状态。
4. 电子签约、企业 IM、工单/采购/CRM 等集成。

### P2：满足企业上线的治理与可靠性

1. OIDC/SAML SSO、SCIM、细粒度 RBAC/ABAC、租户隔离；PostgreSQL 与对象存储替代本地 SQLite/卷。
2. 加密、密钥托管、PII 脱敏、数据保留/删除/法务保全、导出控制和可检索审计日志。
3. 队列、取消/重试/死信、供应商降级与限流；指标、链路追踪、成本归因、告警与 SLO。
4. Playbook 维度的离线评测集、线上抽样复核、发布门禁、漂移和规则更新工作流。

## 架构校正

当前检索模块应恢复为可观测的混合检索：稠密召回 + 关键词召回 + rerank + 来源版本过滤，并对 Recall@K、MRR、引用正确率和风险漏检率设发布门槛。不要仅以“有 RAG/多 Agent”作为工业级判断标准。

## 推荐产品定位

不要第一阶段就复制完整 CLM。更有辨识度的旗舰路径是：

> 面向中国企业法务的“可证据化、可红线、可治理”的合同审查操作系统。

首个可销售垂直切片：上传/邮件收件 → 合同分类 → 对应 Playbook 审查 → Word 红线与证据卡片 → 审批/谈判 → 历史与规则评测闭环。

## 官方来源

- Docusign AI Contract Review Assistant: https://investor.docusign.com/news-and-events/press-releases/news-details/2026/Docusign-Introduces-AI-Contract-Review-Assistant-to-Streamline-Agreements-Workflows/default.aspx
- Docusign CLM: https://www.docusign.com/products/clm
- Ironclad 产品描述: https://legal.ironcladapp.com/fy2026-product-descriptions
- Ironclad AI: https://ironcladapp.com/product/ironclad-ai
- LegalOn 产品说明: https://www.legalontech.com/what-is-legalon
