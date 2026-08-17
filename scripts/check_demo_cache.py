"""验证离线缓存为真实 pipeline 导出（meta.mock 必须为 False）。"""
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台 ✅ 崩溃修复
except Exception:
    pass

root = Path(__file__).resolve().parent.parent  # S9：绝对路径（与 config.ROOT 一致）
ok = True
for p in sorted((root / "frontend/public/reports").glob("*.json")):
    r = json.loads(p.read_text(encoding="utf-8"))
    mock = r["meta"]["mock"]
    print(f"  {p.name}: mock={mock} risks={r['summary']['total']} mode={r['meta']['reviewMode']}")
    if mock is not False:
        ok = False
print("OK: 离线缓存全部为真实 pipeline 导出 ✅" if ok else "FAIL: 存在 mock 缓存")
