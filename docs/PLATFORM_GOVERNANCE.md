# 本地治理与评测

本文件记录当前仓库内可运行的治理能力。它们使用 SQLite 适配器，运行数据库必须放在版本控制之外。

## 任务队列

`app/task_queue.py` 提供带事务领取的本地队列。队列按租户隔离，只接受任务类型和白名单引用 ID，禁止写入合同正文、模型提示词、报告全文或密钥。任务状态包括 `queued`、`running`、`retry_wait`、`succeeded`、`dead` 和 `cancelled`。

领取使用 worker lease；运行中的 worker 会定期 heartbeat，`requeue_stale()` 可将真正失联 worker 的任务放回重试队列。失败任务按最大尝试次数进入死信，管理员可以调用 `retry_dead()` 将其重新排队。审查上传会创建 `review` 任务；审查运行的取消接口为 `POST /review-runs/{id}/cancel`，取消获胜时流水线不会把结果写回已取消运行。

由于系统约束是不在运行数据库保存合同正文，进程重启后不能凭队列记录恢复缺少正文的审查任务。生产部署需要把正文放入合规的加密对象存储后，再替换此适配器。

管理员或法务复核员可通过 `GET /platform/tasks` 查看队列元数据；管理员可用 `POST /platform/tasks/{id}/retry` 重试死信、`POST /platform/tasks/requeue-stale` 回收失联 worker 任务，管理员/法务复核员可用 `POST /platform/tasks/{id}/cancel` 取消任务。设置页提供同一组操作的轻量队列面板。

队列运维接口只返回当前租户的任务，任务详情中的 payload 仍仅包含引用字段。

## 外部集成

`IntegrationConfigStore` 保存租户级的通用 webhook、企业 IM、工单、采购/CRM 和电子签约配置；`GET/PUT/DELETE /platform/integrations/{kind}` 仅限管理员，读取结果只返回 `hasSecret`，不回显密钥。协作状态变化会生成不含合同正文的摘要事件并通过已启用适配器派发，签名、HTTPS allowlist、超时、无重定向和幂等键由 `workflow_automation.py` 强制执行。出站失败最多自动重试 3 次，最终失败会记录 `alert=true`；管理员可用 `GET /platform/integrations/deliveries` 查看脱敏投递记录。具体平台字段和账号仍需在部署环境中配置并联调。

## 备份与恢复

`python scripts/backup_data.py backup data backups/<timestamp>` 使用 SQLite 在线备份和 SHA-256 清单，仅复制 `*.db`。`restore` 会先校验清单并原子恢复到一个不存在的新目录；不会复制密钥、设置、独立上传文件或模型缓存。资产数据库中已保存的合同正文会随数据库备份，生产环境必须把备份目录交给加密对象存储并定期演练恢复。

## 保全与保留期

`app/data_lifecycle.py` 保存租户范围的法律保全和删除审计，不保存合同内容。资产删除采用软删除，保全中的资产不能删除；管理员可以先执行 dry-run，再执行：

```text
POST /contract-assets/{assetId}/delete
POST /platform/data-lifecycle/purge
```

保全接口为 `POST /platform/legal-holds`、`DELETE /platform/legal-holds/{resourceType}/{resourceId}`，资源类型覆盖 `asset`、`review_run`、`collaboration_request` 和 `playbook_version`；事件可从 `GET /platform/data-lifecycle/events` 读取。审查运行支持按保留期筛选已完成、失败或已取消的运行并清理其报告、处置、反馈、trace 和事件；协作单仅清理终态请求，Playbook 仅清理归档版本。

清理请求默认 `dryRun=true`；只有管理员可以使用 `dryRun=false`。清理前必须由法务或合规负责人确认保留期限、保全范围和删除授权。

## 检索评测门禁

`HybridRetriever` 在有本地 `embed_fn` 时执行稠密 + 关键词归一化合并和确定性 rerank；没有向量函数时安全回退为可解释的词法召回。每个命中保留策略、词法分数、稠密分数和匹配词项。

离线评测使用 `app.evaluation_gate.evaluate_retrieval()`，输出 Recall@K、MRR 和引用准确率。命令行入口：

```text
python scripts/evaluate_retrieval.py eval/retrieval-cases.json --k 5
```

输入只需要查询、标准答案 ID 和已排序结果 ID，不需要合同正文。未达到阈值时命令以退出码 1 结束，可接入 CI 发布门禁。

仓库 CI 使用 `tests/fixtures/retrieval_cases.json` 执行一组不含合同正文的门禁样例。

## 指标

`GET /platform/metrics` 仍返回原有运行数、错误数、平均延迟和 token 计数，并增加 p50/p95 延迟、错误率和 SLO 状态。当前阈值是 p95 不超过 1000ms、错误率不超过 5%，阈值属于本地适配器默认值，生产环境应按真实 SLA 配置告警。

## 生产化边界

当前实现验证的是本地适配器和业务规则。PostgreSQL、对象存储、分布式队列、OIDC/SAML、KMS、PII 脱敏、跨节点调度和具体外部平台联调仍需在目标部署环境中实现与验收。
