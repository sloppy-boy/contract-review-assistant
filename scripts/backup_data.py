"""Create or restore a safe local runtime database backup.

Examples:
  python scripts/backup_data.py backup data backups/2026-09-08
  python scripts/backup_data.py restore backups/2026-09-08 data-restored
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backup import backup_data, restore_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up or restore allowlisted CRA SQLite databases")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("source", type=Path)
    backup.add_argument("destination", type=Path)
    restore = subparsers.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = backup_data(args.source, args.destination) if args.command == "backup" else restore_data(args.source, args.destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
