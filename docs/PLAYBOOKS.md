# Playbook 版本与审查快照

本模块覆盖一期 Playbook 领域、版本与运行绑定，并支持声明式规则执行、逐项依据、管理工作台、红线包和版本影响分析。通过 API 可管理 Playbook 版本，提交审查时固定版本内容，并从运行、事件、报告元数据中追溯。`snapshot_only` 仅用于没有自定义规则执行记录的旧运行或内置兼容基线；新运行使用自定义规则时会记录 `playbookApplication: "executed"` 及执行结果。

## 版本语义

- 新建 Playbook 得到第 1 版草稿（`draft`），规则可以暂时为空。
- 更新仅作用于草稿；发布前必须有完整规则，规则 ID 在版本内唯一。
- 发布为 `active` 时冻结内容、SHA-256、创建人/时间和发布人/时间。生效版本内容不能覆盖。
- 从已有版本复制得到新的递增版本草稿。允许多个生效版本并存，由调用方显式选择版本号。
- 归档为 `archived` 后不能用于新审查，也不能再次编辑或发布；可以复制成新版本。
- 归档只改变生命周期和归档记录，已经发布的 `snapshot` 保持不变。
- 每次创建、更新、发布、归档均追加包含租户、版本、操作者与时间的事件。

`contentHash` 对内容的规范 UTF-8 JSON 计算 SHA-256。内容包括适用范围与全部规则；快照读取时校验内容哈希及版本身份。哈希用于完整性检查，不是签名或防数据库管理员篡改机制。

## 接口

开发环境使用本地身份；生产环境使用已有 `X-API-Key` 认证。租户和操作者来自认证身份，不能通过请求体指定。写入允许 `admin` / `legal_reviewer`，读取按租户过滤。

以下路径在生产单容器中也可使用 `/api` 前缀。

| 方法与路径 | 请求 / 返回 |
|---|---|
| `POST /playbooks` | 提交内容，返回第 1 版草稿，201 |
| `GET /playbooks` | `{"playbooks": [...]}`，每个 Playbook 的最新版本（可能为草稿） |
| `GET /playbooks/{id}/versions` | `{"versions": [...]}`，按版本倒序 |
| `GET /playbooks/{id}/versions/{version}` | 完整版本记录及发布快照 |
| `PUT /playbooks/{id}/versions/{version}` | 用完整内容替换草稿 |
| `POST /playbooks/{id}/versions` | `{"sourceVersion": 1}`，复制为新版本，201 |
| `POST /playbooks/{id}/versions/{version}/publish` | 发布，无请求体 |
| `POST /playbooks/{id}/versions/{version}/archive` | 归档，无请求体 |
| `GET /playbooks/{id}/events` | `{"events": [...]}`，按事件顺序 |

未认证返回 401，写权限不足返回 403，不存在或其他租户的版本返回 404，生命周期/适用范围冲突返回 409，字段不合法返回 422。

创建与更新使用同一内容结构。下面是用于接口演示的合成规则，未经法务验证：

```json
{
  "name": "采购规则接口示例",
  "contractType": "purchase",
  "jurisdiction": "CN",
  "businessScenario": "general",
  "effectiveScope": ["procurement"],
  "rules": [
    {
      "id": "payment-1",
      "categoryId": "payment_invoice",
      "riskType": "付款条件不明确",
      "severity": "medium",
      "triggerCondition": "付款条件仅由一方确认",
      "reviewQuestion": "付款条件是否客观且有明确期限？",
      "acceptableCondition": "双方明确约定付款条件与期限",
      "suggestedClause": "双方应在合同中明确约定付款条件和期限。",
      "escalationPolicy": "提交法务确认"
    }
  ]
}
```

`severity` 为 `high` / `medium` / `low`；`categoryId` 为可选的未来 worker 路由标识。其他规则字段必须非空。`effectiveScope` 为一个或多个业务范围标签；`["*"]` 表示范围不限。合同类型、法域、业务场景必须与审查请求精确匹配，`*` 不用于这些字段。

## 提交与读取审查

`POST /upload` 保留原有文件/文本表单，新增：

| 表单字段 | 约束 / 默认值 |
|---|---|
| `playbook_id` | 与 `playbook_version` 同时提供 |
| `playbook_version` | 正整数，明确选定生效版本 |
| `jurisdiction` | 默认 `CN` |
| `business_scenario` | 默认 `general` |
| `effective_scope` | 默认 `*`；对于受限 Playbook，提供其生效范围中的一个标签 |

服务端先解析当前租户的生效版本及适用范围，再将快照写入运行，最后启动异步任务。非法选择不会创建任务。模型未配置时仍沿用现有 503 行为。

旧客户端省略 Playbook 选择时，系统绑定从现有风险矩阵生成的内置基线。内置基线仅适用于 `CN` / `general`，其 ID 包含完整内容哈希，合同类型或矩阵内容变化会产生不同 ID；版本号为 1。固定的系统创建/发布日期表示此内置适配器的版本发布日期，操作者为 `system:risk-matrix`，不代表人工审核发布。内置基线作为完整快照保存在运行中，不作为可编辑条目出现在 `/playbooks`。

- `GET /report/{taskId}`：`playbookSnapshot` 包含版本身份、完整内容、哈希、创建/发布信息。
- 完成报告：`report.meta.playbookSnapshot` 来自持久化运行，覆盖流水线返回的冲突字段。
- `GET /review-runs`：历史列表保留绑定快照。
- `GET /review-runs/{taskId}/events`：`created` 事件的 `data.playbook` 包含 ID、版本与哈希。
- 运行失败、版本归档、发布后续版本、存储重开均不更换已绑定快照。

详情和事件读取均检查当前租户。旧数据库自动增加 nullable 快照列；已有历史运行的 `playbookSnapshot` 为 `null`，不补造过去的审查标准。

## 存储与验证

`PlaybookStore` 使用 `REVIEW_RUNS_PATH` 同目录的 `playbooks.db`；版本号分配与生命周期写入使用 `BEGIN IMMEDIATE` 事务。该数据库及 SQLite 辅助文件已加入忽略规则。测试使用合成数据、临时目录和强制 mock，不调用真实审查模型。

测试覆盖领域校验、版本生命周期、并发编号、完整性、租户访问、历史迁移、队列等待期间版本变更、报告元数据防覆盖和 A/B/C 流水线传递：

```text
python -m pytest tests/ -q
cd frontend
npm run build
```

运行完整测试时可将 `REVIEW_RUNS_PATH` 指向临时目录，将 `CONTRACT_REVIEW_SETTINGS` 指向临时空配置路径，并设置 `DSH_FORCE_MOCK=1`、`APP_ENV=development`，隔离本机运行数据库与配置。
