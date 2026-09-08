# 二期合同资产与履约实施计划

**Goal:** 完成文档导入、合同库与检索、义务/关键日期站内提醒；外部平台连接待用户指定平台后联调。

**Architecture:** 解析器仅处理上传字节并返回带定位的文本；AssetStore 使用独立 SQLite 保存原始字节、提取结果、权限、义务及通知。路由在身份边界执行校验，前端新增合同资产页。检索只对可读资产计算，问答返回原文摘录和引用；可选语义编码器不冒充已启用。

**Tech Stack:** FastAPI/Pydantic/SQLite、python-docx/pypdf、可选本地 Tesseract 与 PDF 光栅器、Vue。

**Spec:** docs/UPGRADE_ROADMAP.md 二期 2–4 项，用户“完成后续任务”。

## 约束

- 保留现有未提交改动，不提交密钥、原文、数据库或构建文件。所有测试使用临时合成数据。
- 文档权限为 tenant 或 private；private 仅创建者、显式共享成员和同租户管理员可读。写入仅创建者和管理员，reader 禁止写。
- 所有更新校验 expectedRevision，角色/租户/资产权限在检索、问答、下载、义务、通知中保持一致。
- OCR 无可用引擎时明确拒绝扫描文档，不产生空内容的“成功合同”。不自动下载语言模型。
- 外部 IM/工单/CRM/签约平台尚未指定，不伪造集成成功；本轮不向外发送合同或消息。

## Task 1 文档解析

Files: app/document_import.py, tests/test_document_import.py。

- [x] 先写并观察 TXT/DOCX 段落表格、PDF 页定位、空文件/损坏/大小限制、扫描 PDF 的 OCR 必需失败测试。
- [x] 实现 `extract_document(filename: str, data: bytes, *, ocr=None) -> dict`。返回 `text`, `segments`（id/location/text）, `metadata`（保守候选，带来源，不推断无证据字段）, `warnings`, `method`。
- [x] 限制 10 MiB 原始大小、100 PDF 页、1,000,000 提取字符、DOCX 解压 50 MiB；失败 ValueError，缺少 OCR RuntimeError。
- [x] 提供本地可选 OCR 接口和明确依赖说明；不执行宏、不请求文档外部关系、不保留临时文件。

## Task 2 合同资产存储、检索与义务

Files: app/asset_store.py, tests/test_asset_store.py。

- [x] 先测试持久化、租户与 private 权限、修订冲突、检索引用、下载原件、义务与去重提醒。
- [x] `AssetStore(path)` 方法（参数采用关键字 actor: Principal）：
  `create(filename, data, extracted, *, actor, visibility='private', tags=[], shared_with=[])`, `get(id, *, actor)`, `list_assets(*, actor, query='', tag='')`, `update(id, *, actor, expected_revision, title, tags, visibility, shared_with)`, `original(id, *, actor)` 返回 `(filename, bytes)`。
  资产字段 `id,title,filename,tenantId,createdBy,createdAt,revision,visibility,sharedWith,tags,text,segments,metadata,warnings,method`；列表不返回正文或原始字节。
- [x] `search(query, *, actor, limit=20)` 返回引用列表，每项 assetId/title/segmentId/location/text/score，只读可见资产。`ask(question, *, actor)` 返回 answer/citations/mode='extractive'，无依据明确无法回答，不生成无来源结论。
- [x] `add_obligation(id, *, actor, expected_revision, title, due_at, kind)` (kind obligation/renewal/key_date)，`complete_obligation(id, obligation_id, *, actor, expected_revision)`，`obligations(id, *, actor)`；UTC 带时区，义务到期时间允许过去以便记录逾期。
- [x] `dispatch_reminders(*, actor, now=None)` 只为 actor 可读且未完成到期义务生成该身份的站内提醒，唯一键防重复；`notifications(*, actor)` 与 `mark_read(notification_id, *, actor)` 收件人及当前权限校验。

## Task 3 API 与界面

Files: app/asset_api.py, app/api.py, frontend/src/asset-api.js, frontend/src/views/AssetsView.vue, frontend/src/App.vue, tests/test_asset_api.py, frontend/tests/asset-api.test.js。

- [x] API 红测后接入 `/contract-assets/import` 多文件（最多 10，每文件独立结果），`/contract-assets` 列表，`/contract-assets/search`，`/contract-assets/ask`，详情/PATCH/原件下载，义务新建/完成，提醒列表/检查/已读。
- [x] 认证来自 current_principal，sharedWith 必须为同租户现有成员；错误映射 403/404/409/422/503。
- [x] 前端提供批量上传反馈、列表/标签筛选、共享设置、正文来源、搜索引用与摘录问答、义务和通知。切换身份清理状态并防止陈旧异步回复写回。
- [x] 合同可送入既有工作台审查流程，复用上传 API；不绕过 Playbook 绑定逻辑。

## Task 4 验证与边界

- [x] 全量 pytest、前端测试、构建、合成浏览器流程；复核权限与错误副作用。
- [x] 更新路线图与能力配置文档，明确 OCR、语义检索、外部平台的可用条件及未联调边界。

## 执行记录

现有协作基线 168 pytest / 8 frontend tests。采用独立任务代理实现解析与存储，主任务实现 API/前端并验证接口。保留当前工作树以延续已授权的未提交功能；不自动提交或迁移已有数据。

完成记录：解析 23 项、存储 12 项、语义重排 21 项、资产 API/后台提醒 9 项均通过；完整后端 233 passed，前端 11 passed，生产构建通过。浏览器使用临时合成合同验证导入、引用问答、履约提醒和送入工作台。独立审查发现并修复管理员代建义务导致资产所有者漏提醒；另以失败测试发现并修复 `/assets` API 与前端静态资源目录冲突，最终 API 使用 `/contract-assets`。OCR 和语义模型保持本地可选，未下载模型或向外部发送数据。
