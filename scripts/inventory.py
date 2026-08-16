"""收集测试资产统计，供资料文档使用。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for split in ("dev", "test"):
    contracts = len(list((Path("eval/dataset") / split / "contracts").glob("*.txt")))
    labels = len(list((Path("eval/dataset") / split / "labels").glob("*.json")))
    print(f"  {split}: contracts={contracts} labels={labels}")
demo_files = len(list(Path("eval/dataset/demo").glob("*")))
print(f"  demo: {demo_files} 个文件")
print(f"  标注指南存在: {Path('eval/dataset/标注指南.md').exists()}")
meta = json.loads(Path("eval/dataset/meta.json").read_text(encoding="utf-8"))
print(f"  人工抽查: {len(meta['manualAudit'])} 份, 变体占比 {meta['variantShare']:.0%}")

reports = list(Path("eval/output").rglob("*.json"))
print(f"  eval/output 报告: {len(reports)} 份")
modes = {}
for p in reports:
    modes[p.parts[2]] = modes.get(p.parts[2], 0) + 1
print(f"  按档位: {modes}")

tests = len([l for l in Path("tests/test_core.py").read_text(encoding="utf-8").splitlines() if l.strip().startswith("def test_")])
print(f"  tests/test_core.py 单测: {tests} 个")
