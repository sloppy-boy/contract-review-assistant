# 审查协作闭环

本模块是二期首个切片，入口为在线模式的“审查协作”页。先创建发起单并提交，指派处理人、审批人和截止时间；在工作台完成合同审查后，关联同类型的已完成报告，提交审批。审批人填写理由后批准或退回补充。退回后可以重新关联报告并再次提交审批。

## 身份与操作权限

生产模式沿用 `APP_ENV=production` 与 `CRA_API_KEYS` 的工作区认证，前端沿用设置页的工作区密钥。成员接口只返回同租户的 subject/role。开发模式只有 `local-developer` 管理员，允许处理人与审批人为同一人；当前不强制职责分离。

| 操作 | 权限 |
| --- | --- |
| 查看事项、事件、报告快照 | 同租户成员，包括 reader |
| 创建、评论 | admin、legal_reviewer、requester |
| 提交、指派、取消 | 发起人或 admin，且具备写入角色 |
| 关联报告、提交审批 | 指定处理人或 admin，且具备审查角色 |
| 批准、退回 | 指定审批人，且角色为 admin 或 legal_reviewer |
| 读取通知、标为已读 | 通知收件人本人 |

## 状态、时间与审批依据

状态为 `draft → submitted → in_review → pending_approval → approved`。审批退回进入 `changes_requested`，可补充后再次审批；非终态允许取消为 `cancelled`。批准或取消后不能重新指派或再次审批，仍可追加评论。

每次修改必须携带正整数 `expectedRevision`。事务内校验并递增版本，同时追加事件和通知；陈旧版本返回 409，前端刷新详情后由用户重新操作。跨租户对象返回 404。

`dueAt` 必须为带时区的未来时间，界面按浏览器本地时间输入。服务端保存 UTC，查询时计算 `isOverdue`；批准和取消后不再标为超期。SLA 计算支持工作日、时区和节假日配置；运行时按周期生成到期前、到期和逾期升级的幂等站内提醒。

关联运行须已完成、属于同租户且合同类型一致。提交审批时复制当前报告及人工处置到 `approvalSnapshot`，其中包括报告保留的 Playbook 元数据；后续原报告或处置变化不改写该轮依据。`approval_requested` 事件保留每一轮快照，事项详情提供最新一轮。页面显示风险、处置理由、法律依据，并可展开完整冻结证据。

Playbook 运行会保留版本快照；使用自定义规则的运行还会保留规则执行结果与逐项依据。

## API

以下路径均以 `/collaboration` 为前缀。JSON 请求不接受未声明字段；生产环境使用既有 `X-API-Key` 认证。

| 方法与路径 | 请求内容或结果 |
| --- | --- |
| GET `/me`、`/members` | 当前身份、同租户成员 |
| POST `/requests` | `title`、`contractType`、可选 `description`；返回 201 |
| GET `/requests?status=...` | `{requests: [...]}`，列表不含完整审批快照 |
| GET `/requests/{id}`、`/requests/{id}/events` | 事项详情、`{events: [...]}` |
| POST `/requests/{id}/submit` | `expectedRevision` |
| POST `/requests/{id}/assign` | `expectedRevision`、`assignee`、`approver`、`dueAt` |
| POST `/requests/{id}/run` | `expectedRevision`、`runId` |
| POST `/requests/{id}/request-approval` | `expectedRevision`；服务端读取报告和处置 |
| POST `/requests/{id}/decide` | `expectedRevision`、`decision`（approved/changes_requested）、非空 `reason` |
| POST `/requests/{id}/cancel` | `expectedRevision`、非空 `reason` |
| POST `/requests/{id}/comments` | `expectedRevision`、`body`、可选 `mentions` subject 数组 |
| GET `/notifications?unread_only=true` | `{notifications: [...]}` |
| POST `/notifications/{id}/read` | 无请求体，重复标记保持同一已读时间 |

评论的 mentions 必须是同租户成员；正文中的 `@文字` 不会自动解析为收件人。通知根据提交、指派、审批与评论事件产生，排除操作者本人，保存在站内收件箱。配置并启用外部集成后，协作状态会额外发送不含合同正文的摘要事件；未配置时不会出站。

## 存储与验证

事项、事件、审批快照和通知保存在 `REVIEW_RUNS_PATH` 同目录的 `collaboration.db`。这些都是运行数据，不应提交到 Git；数据库路径设置到其他目录时也应自行保持在版本控制外。

运行 `python -m pytest tests/ -q`、`npm --prefix frontend test` 和 `npm --prefix frontend run build`。安装 agent-browser CLI 后，可运行 `python scripts/check_collaboration_ui.py` 验证合成浏览器闭环；脚本使用临时数据库，不调用模型。可选截图应输出到忽略目录：`--screenshot .tmp/collaboration-ui.png`。

文档导入、合同仓库、跨合同检索和履约提醒已在合同资产模块提供；外部 IM、工单、采购/CRM 与电子签约平台仍待目标平台配置后联调。
