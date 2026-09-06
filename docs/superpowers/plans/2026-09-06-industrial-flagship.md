# 工业级旗舰合同审查助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可审计、可隔离、可观测、可评测、可部署的合同审查平台基线。

**Architecture:** 在现有 LangGraph 流水线之外新增身份、审计、追踪、反馈与检索模块；模块通过小接口接入 API，并以 SQLite 提供默认实现。

**Tech Stack:** FastAPI、Pydantic、SQLite、Vue 3、Docker、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-09-06-industrial-flagship-design.md`

## Global Constraints

- 生产环境不得启用匿名访问；密钥只从环境变量读取。
- 推理原始报告不可被人工动作覆盖。
- 所有新增行为先写失败测试，再写最小实现。

---

### Task 1: 身份、租户和审计边界

**Files:** `app/security.py`、`app/audit.py`、`app/api.py`、`tests/test_industrial.py`

- [ ] 写出无效 API key、越权租户和审计事件的失败测试。
- [ ] 实现角色检查、租户作用域和追加式审计记录。
- [ ] 运行 `python -m pytest tests/test_industrial.py -v`。

### Task 2: Trace、反馈与评测门禁

**Files:** `app/observability.py`、`app/feedback.py`、`app/evaluation_gate.py`、`tests/test_industrial.py`

- [ ] 写出 trace 聚合、脱敏反馈导出和门禁失败的测试。
- [ ] 实现 SQLite 默认 Adapter 与 API 投影。
- [ ] 运行定向及完整后端测试。

### Task 3: 可解释法规检索

**Files:** `app/legal/retrieval.py`、`app/nodes/report.py`、`tests/test_industrial.py`

- [ ] 写出版本过滤、混合得分排序和来源解释的失败测试。
- [ ] 实现可替换检索器并写入报告证据。
- [ ] 运行法规与报告回归测试。

### Task 4: 生产交付资产

**Files:** `Dockerfile`、`docker-compose.yml`、`.github/workflows/ci.yml`、`README.md`

- [ ] 增加非 root 镜像、健康检查、生产环境变量清单与 CI 门禁。
- [ ] 验证 Python 测试与前端生产构建。
