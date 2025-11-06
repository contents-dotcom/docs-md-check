# tools/run_strict_restore.py
# -*- coding: utf-8 -*-
"""
③ 復元=原文一致チェックを起動するだけのラッパー。
    python tools/run_strict_restore.py
"""

from __future__ import annotations
import subprocess
import sys
from pathlib import Path

def main() -> int:
    script = Path("tokenize_markdown.py")
    if not script.exists():
        print(f"[ERROR] {script} が見つかりません。リポジトリのルートに配置してください。")
        return 2
    try:
        # 「. を対象、--strict-restore を有効」の固定呼び出し
        cmd = [sys.executable, str(script), ".", "--strict-restore"]
        print("[INFO] run:", " ".join(cmd))
        return subprocess.call(cmd)
    except Exception as e:
        print(f"[ERROR] 実行に失敗しました: {e}")
        return 2

if __name__ == "__main__":
    sys.exit(main())
