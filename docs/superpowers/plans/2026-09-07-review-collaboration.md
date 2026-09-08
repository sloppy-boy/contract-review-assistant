# 二期首个切片：审查协作闭环

**Goal:** 交付可操作的审查发起、指派/SLA、报告关联、审批、评论/@提及和站内通知。

**Architecture:** 独立 `CollaborationStore` 保存事项、事件、评论、审批快照与站内通知；通过认证身份校验角色/参与人，通过已有 `ReviewRunStore` 关联完成报告。Vue 新增“审查协作”页，沿用现有组件及样式。

**Tech Stack:** Python/Pydantic/SQLite/FastAPI、Vue 3/Element Plus、pytest；优先使用已有依赖。

**Spec:** `docs/UPGRADE_ROADMAP.md` 二期第 1 项。用户本轮明确选择先做审查协作闭环。

## 约束和决策

- 保留上一轮未提交的 Playbook 实现；继续测试优先，不提交密钥、运行数据库或合同原文。
- 本次交付二期第一个模块。文档导入、合同库、跨合同检索、外部 IM/邮件、电子签约和履约提醒不在本轮范围。
- 发起单与一次模型运行分离：同一事项在退回后可关联另一份完成报告。
- 状态：`draft → submitted → in_review → pending_approval → approved`；审批可退回 `changes_requested`，重新关联报告后再次提交；非终态可取消。
- 身份来自现有工作区配置。创建允许 `admin/legal_reviewer/requester`；指派人是发起人或 admin，处理人和审批人必须是同租户的 admin/legal_reviewer。审批仅由指定审批人操作。本期不额外强制审查人与审批人分离。
- 同租户成员可读取事项；修改操作校验角色及参与关系。跨租户读取返回 404。成员列表仅返回 subject/role，绝不返回访问密钥。
- SLA 是显式带时区截止时间，不推断节假日/工作日；超期状态由查询时计算，已批准/取消的事项不继续显示待办超期。
- 每次事项修改检查 `expectedRevision` 并在事务内递增 revision，防止陈旧页面覆盖新状态或重复审批。
- 关联报告须已完成、同租户且合同类型一致。提交审批时冻结报告、人工处置与 Playbook 引用副本，后续原报告变化不改写审批依据。
- 评论/@提及使用显式 mentions subject 数组，服务端验证成员；通知在同一事务写入，按租户与收件人隔离，标为已读幂等。

## Task 1：协作模型与持久化

Files: `app/collaboration.py`, `tests/test_collaboration.py`。

- [x] 先验证状态流转、角色/参与关系、乐观版本冲突、SLA、报告快照、评论/@提及及通知隔离失败。
- [x] 实现模型、SQLite 事务和事件/通知原子追加。
- [x] 覆盖批准、退回后再提交、取消、重开恢复以及跨租户失败无副作用。

## Task 2：认证 API 与已有审查运行接入

Files: `app/collaboration_api.py`, `app/api.py`, `tests/test_collaboration_api.py`。

- [x] 先测试 HTTP 创建、提交、指派、关联报告、提请审批、批准/退回、评论、通知已读。
- [x] 使用可注入的 store/身份/成员依赖注册 `/collaboration` 路由。
- [x] 通过现有运行存储验证报告资格，在提交审批时冻结报告及人工处置。
- [x] 校验 401/403/404/409/422 行为及未授权操作不产生事件/通知。

## Task 3：协作界面

Files: `frontend/src/views/CollaborationView.vue`, `frontend/src/collaboration-api.js`, `frontend/src/App.vue` 与相关前端测试。

- [x] 使用现有应用导航、panel、表单和表格完成列表/状态过滤、发起单、详情/操作和时间线。
- [x] 支持成员选择、截止时间、选择已完成审查报告、审批理由、评论/@提及和通知已读。
- [x] 先测请求封装的真实 HTTP 方法/路径/请求体/异常传播；浏览器验证完整合成协作流程。
- [x] 离线演示模式不写入真实事项；生产鉴权复用工作区密钥。

## Task 4：交付门禁

- [x] 后端全量测试（基线 128 项）、新增前端测试及生产构建通过。
- [x] 独立代码审查并修复正确性问题；完成必要回归。
- [x] 更新路线图、接口文档和测试记录，检查差异和密钥/运行数据忽略状态。


## 验证记录（2026-09-07）

- 存储：26 项先因缺少模块失败，实现后通过；补充审批时报告更新与 model_copy 校验 2 项，先 2 failed / 26 passed，修复后 28 passed。
- API：12 项先因缺少路由返回 405 失败，实现后 12 passed；协作存储与 API 合计 40 passed。
- 前端 HTTP：6 项先因缺少模块失败，实现后通过；异步状态覆盖的 2 项回归先失败，再修复通过，合计 8 passed。
- 独立审查发现并修复事项切换竞态与冻结证据展示不足；浏览器使用临时合成数据验证完整流程。
- 全量后端 168 passed；前端测试与生产构建通过。git diff --check 通过，运行数据库、密钥配置、截图及构建输出仍被忽略。未执行 Git 提交。
