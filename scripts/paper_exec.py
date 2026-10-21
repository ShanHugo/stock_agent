#!/usr/bin/env python3
"""排程用：09:30 以即時報價執行模擬帳戶的待執行單（等同 python scripts/paper_bot.py --execute-now）。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import paper_bot  # noqa: E402

if __name__ == '__main__':
    sys.exit(paper_bot.run_execute(lambda m: print(m, file=sys.stderr), dry_run='--dry-run' in sys.argv))
