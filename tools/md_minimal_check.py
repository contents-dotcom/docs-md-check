# tools/md_minimal_check.py
# -*- coding: utf-8 -*-
"""
最小3チェックのうち ①フェンス未閉じ ②自作トークン混入 を実行し、違反があれば非ゼロ終了で停止します。
（③復元=原文一致 は tokenize_markdown.py --strict-restore で別途実行）
使い方:
    python tools/md_minimal_check.py
pre-commit では pass_filenames:false でリポ全体を走査する想定です。
"""

from __future__ import annotations
from pathlib import Path
import re
import sys

# 生成物はチェック対象外（人間が編集する“素のMD”だけチェック）
def is_generated_md(p: Path) -> bool:
    name = p.name.lower()
    return name.endswith(".masked.md") or name.endswith(".view.md")

# フェンス未閉じチェック：開閉スタックで位置も出す
def check_fence_balance(text: str) -> list[str]:
    errs = []
    lines = text.splitlines()
    fence_pat = re.compile(r"^```(?P<lang>[^\n]*)\s*$")
    stack = []  # (line_no, lang)
    for i, line in enumerate(lines, start=1):
        m = fence_pat.match(line)
        if m:
            if stack and m.group("lang").strip() == "":
                # 空言語で閉じ扱い（開きと同じ記号なのでトグル方式）
                stack.pop()
            else:
                # 開き扱い（単純トグルにせず、開き→閉じの組で運用）
                # 言語指定ありを開きとみなす。閉じは言語なしの ``` を前提。
                lang = m.group("lang").strip()
                if lang:
                    stack.append((i, lang))
                else:
                    if stack:
                        stack.pop()
                    else:
                        errs.append(f"fence: 余分な閉じ ``` （{i} 行目）")
    for ln, lang in stack:
        errs.append(f"fence: 未閉じ ```{lang} （{ln} 行目の開始が未クローズ）")
    # 予備：単純カウントでも差があれば警告
    open_cnt  = len(re.findall(r"(?m)^```[^\n]*$", text))
    close_cnt = len(re.findall(r"(?m)^```[ \t]*$", text))
    if open_cnt != close_cnt and not errs:
        errs.append(f"fence: 開閉数が不一致の可能性 (open={open_cnt}, close={close_cnt})")
    return errs

# 自作トークン混入禁止（自動生成に使う形は原稿に手書きしない）
TOKEN_LIKE = re.compile(r"\{\{(?:CODE|TABLE)_\d{3}\}\}")

def main() -> int:
    root = Path(".")
    md_files = [p for p in root.rglob("*.md") if p.is_file() and not is_generated_md(p)]

    failed = False
    for p in md_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[MD ] {p}: 読み込みエラー: {e}")
            failed = True
            continue

        # ① フェンス未閉じ
        fence_errs = check_fence_balance(text)
        for e in fence_errs:
            print(f"[MD ] {p}: {e}")
            failed = True

        # ② トークン風の手書き混入
        if TOKEN_LIKE.search(text):
            print(f"[MD ] {p}: token: 原稿内に {{CODE_xxx}}/{{TABLE_xxx}} を手書きしないでください")
            failed = True

    if failed:
        print("\n[NG] 上記を修正してから再コミットしてください。")
        return 1

    print("[OK] minimal md checks passed.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
