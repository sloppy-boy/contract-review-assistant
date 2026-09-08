# 合同资产、导入与履约提醒

“合同资产”页提供批量导入、权限、标签、原文定位、跨合同检索、摘录问答、义务/续签/关键日期和站内提醒。导入默认私有；原始文件、提取文本及提醒保存在 `REVIEW_RUNS_PATH` 同目录的 `assets.db`，该运行数据库已由 `.gitignore` 排除。

## 导入与 OCR

支持 UTF-8 TXT/MD、DOCX 和 PDF，每批 1–10 份，每份不超过 10 MiB。DOCX 解压内容限制为 50 MiB；PDF 限制 100 页；最终文本限制 1,000,000 字符。损坏或空文档按文件返回失败，不创建空资产。正文段落、表格行或 PDF 页保留 segment id 与位置。

元数据只提取有明确标签的合同编号、甲乙方、签订日期及金额，并保留来源片段、位置和原文；DOCX/PDF 内置属性也标记文档属性来源。这些字段是待核对候选。

扫描 PDF 或混合 PDF 的无文本页必须配置本地 OCR，不能静默跳过。服务不会下载 OCR 模型或上传文件到第三方。

| 环境变量 | 说明 |
|---|---|
| `CONTRACT_OCR_TESSERACT_CMD` | 本地 Tesseract 路径；未配置表示 OCR 不可用 |
| `CONTRACT_OCR_RASTERIZER_CMD` | Poppler `pdftoppm` 路径，默认 `pdftoppm` |
| `CONTRACT_OCR_TIMEOUT_SECONDS` | 每个子进程超时，默认 30 秒 |
| `CONTRACT_OCR_LANGUAGE` | Tesseract 语言，例如 `chi_sim+eng` |

## 权限、检索与问答

`private` 资产仅创建者、同租户管理员和显式共享成员可读；`tenant` 对同租户成员可读。创建者及同租户管理员可修改资产和履约事项，reader 不能写。详情、下载、全文检索、语义候选、摘录问答、义务和提醒均使用同一访问规则；取消共享后，旧通知也不再可见或可标记已读。所有修改携带 `expectedRevision`，陈旧修改返回 409。

关键词检索返回标题、位置、原文和分数。摘录问答只组合命中片段并返回引用，模式固定为 `extractive`；无依据时明确无法回答。

语义检索是可选本地能力。安装 `requirements-semantic.txt`，并将 `CRA_EMBEDDING_MODEL_PATH` 指向已有 SentenceTransformer 模型目录。加载使用 `local_files_only=True`，不会联网下载；未配置或依赖缺失时接口返回 503。模型只对已通过权限过滤的片段重排。

## 履约提醒

资产可记录 `obligation`、`renewal` 和 `key_date`。服务运行时每分钟检查所有已配置工作区身份，也在读取收件箱时补查。已到期且未完成的事项为每个当前有权读取合同的身份产生一次站内通知；重复检查不会重复创建。撤销合同访问权限会隐藏相关提醒。

当前提醒只在应用内。外部 IM、工单、采购/CRM 与电子签约需要确定目标平台、账号和字段映射后联调。

## API 与验证

API 前缀是 `/contract-assets`，生产单容器中前端使用 `/api/contract-assets`，避免与 `/assets/*.js` 静态构建文件冲突。包含批量 import、列表/详情/PATCH、original、search/ask、obligations 和 notifications/reminders 路径。

```bash
python -m pytest tests/ -q
npm --prefix frontend test
npm --prefix frontend run build
python scripts/check_collaboration_ui.py --assets
```

浏览器脚本使用临时合成合同和数据库，不调用模型、不发送外部消息。截图应写入已忽略的 `.tmp/`。
