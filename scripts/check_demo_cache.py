"""验证离线缓存为真实 pipeline 导出（meta.mock 必须为 False）。"""
import json
from pathlib import Path

ok = True
for p in sorted(Path("frontend/public/reports").glob("*.json")):
    r = json.loads(p.read_text(encoding="utf-8"))
    mock = r["meta"]["mock"]
    print(f"  {p.name}: mock={mock} risks={r['summary']['total']} mode={r['meta']['reviewMode']}")
    if mock is not False:
        ok = False
print("OK: 离线缓存全部为真实 pipeline 导出 ✅" if ok else "FAIL: 存在 mock 缓存")
