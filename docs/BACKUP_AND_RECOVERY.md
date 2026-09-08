# 本地数据备份与恢复

`scripts/backup_data.py` 只处理数据目录中的 `*.db` 文件，使用 SQLite 在线备份和 SHA-256 清单。不会复制 `settings.json`、`.env`、独立上传文件、模型输出或嵌入缓存；资产数据库中已经保存的合同正文仍会随数据库一起备份，因此备份目录必须加密并限制访问。

```powershell
python scripts/backup_data.py backup data backups/2026-09-08
python scripts/backup_data.py restore backups/2026-09-08 data-restored
```

恢复目标必须不存在，避免误覆盖现有数据。恢复前会校验清单中的每个数据库；校验失败时不会创建目标目录。生产环境仍应将备份目录交给企业对象存储，并按企业策略加密、保留和演练恢复。
