"""收集测试资产统计，供资料文档使用。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent  # S9：绝对路径（与 config.ROOT 一致）
for split in ("dev", "test"):
    contracts = len(list((ROOT / "eval/dataset" / split / "contracts").glob("*.txt")))
    labels = len(list((ROOT / "eval/dataset" / split / "labels").glob("*.json")))
    print(f"  {split}: contracts={contracts} labels={labels}")
demo_files = len(list((ROOT / "eval/dataset/demo").glob("*")))
print(f"  demo: {demo_files} 个文件")
print(f"  标注指南存在: {(ROOT / 'eval/dataset/标注指南.md').exists()}")
meta = json.loads((ROOT / "eval/dataset/meta.json").read_text(encoding="utf-8"))
print(f"  人工抽查: {len(meta['manualAudit'])} 份, 变体占比 {meta['variantShare']:.0%}")

reports = list((ROOT / "eval/output").rglob("*.json"))
print(f"  eval/output 报告: {len(reports)} 份")
modes = {}
for p in reports:
    modes[p.parts[-3]] = modes.get(p.parts[-3], 0) + 1  # 绝对路径下档位名恒为倒数第 3 段
print(f"  按档位: {modes}")

tests = len([l for l in (ROOT / "tests/test_core.py").read_text(encoding="utf-8").splitlines() if l.strip().startswith("def test_")])
print(f"  tests/test_core.py 单测: {tests} 个")
