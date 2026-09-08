# 部署与运维清单

## 本地/单机

```powershell
pip install -r requirements.txt
python -m pytest tests/ -q
Set-Location frontend; npm ci; npm run build; Set-Location ..
python scripts/run-server.py
```

应用使用 `/health` 作为容器健康检查，运行数据库放在 `REVIEW_RUNS_PATH` 所在目录。不要把 `.env`、`settings.json`、`data/*.db` 或上传原件加入版本控制。

## 备份与恢复

```powershell
python scripts/backup_data.py backup data backups/$(Get-Date -Format yyyyMMdd-HHmmss)
python scripts/backup_data.py restore backups/20260908-120000 data-restored
```

备份工具只复制 SQLite 数据库并校验 SHA-256；恢复到新目录后，再通过 `REVIEW_RUNS_PATH` 指向该目录完成切换。切换前应停止写入流量并保留旧目录，确认健康检查和抽样数据后再清理。

## 生产化切换前

- 可先运行 `python scripts/stress_local_queue.py --tasks 500 --workers 16` 检查本地队列在竞争领取下不丢任务；脚本使用临时数据库，结束后自动删除。
- 将 SQLite 存储、任务队列和原件目录替换为企业 PostgreSQL、分布式队列和加密对象存储适配器。
- 配置 OIDC/SAML、KMS、网络出口 allowlist、日志脱敏和备份保留策略。
- 配置具体外部 IM、工单、采购/CRM、电子签平台，并用契约测试验证签名、幂等、重试和失败告警。
- 在目标环境执行压测、故障恢复、权限越权、备份恢复和人工法务验收。
