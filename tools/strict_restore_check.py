# tools/strict_restore_check.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, re, sys, shutil
from pathlib import Path
from typing import Dict, Tuple, List

def uprint(*args, **kwargs):
    text = " ".join(str(a) for a in args)
    stream = kwargs.get("file", sys.stdout)
    enc = getattr(stream, "encoding", None) or "utf-8"
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        stream.write(text.encode(enc, errors="replace").decode(enc, errors="replace") + "\n")

FENCED_CODE_RE = re.compile(r"(^[ \t]*```[^\n]*\n.*?^[ \t]*```[ \t]*\n?)", re.MULTILINE | re.DOTALL)
INDENT_CODE_RE = re.compile(r"((?:(?:^(?:[ ]{4}|\t))(?![-*+\s]|\d+\.\s).*\n){2,})", re.MULTILINE)
TABLE_BLOCK_RE  = re.compile(r"(?P<table>^[ \t]*\|.*\|\s*\n[ \t]*\|[ \t]*:?[-=]{3,}.*\|\s*\n(?:[ \t]*\|.*\|\s*\n)+)", re.MULTILINE)

def _replace_all(pattern: re.Pattern, kind: str, start_idx: int, s: str, store: Dict[str, str]) -> Tuple[str, int]:
    """検査専用：改行など“1文字も足さない/引かない”でトークン化"""
    idx = start_idx
    out: List[str] = []
    last = 0
    for m in pattern.finditer(s):
        block = m.group(0)
        token = f"{{{{{kind}_{idx:03d}}}}}"
        store[token] = block
        # ★修正点：検査では前後の空行を一切挿入しない（= 完全可逆）
        out.append(s[last:m.start()])
        out.append(token)
        last = m.end()
        idx += 1
    out.append(s[last:])
    return "".join(out), idx

def extract_protected(src_text: str) -> Tuple[str, Dict[str, str]]:
    text = src_text.lstrip("\ufeff")
    store: Dict[str, str] = {}
    code_idx = 1; table_idx = 1
    text, code_idx  = _replace_all(FENCED_CODE_RE, "CODE",  code_idx,  text, store)
    text, code_idx  = _replace_all(INDENT_CODE_RE, "CODE",  code_idx,  text, store)
    text, table_idx = _replace_all(TABLE_BLOCK_RE, "TABLE", table_idx, text, store)
    return text, store

def restore_protected(masked_text: str, store: Dict[str, str]) -> str:
    out = masked_text
    for token in sorted(store.keys(), key=len, reverse=True):
        out = out.replace(token, store[token])
    return out

def is_generated_md(p: Path) -> bool:
    n = p.name.lower()
    return n.endswith(".masked.md") or n.endswith(".view.md")

def iter_md_files(root: Path) -> List[Path]:
    return [p for p in root.rglob("*.md") if p.is_file() and not is_generated_md(p)]

def unified_snippet(a: str, b: str, context: int = 2) -> str:
    a_lines = a.splitlines(); b_lines = b.splitlines()
    n = min(len(a_lines), len(b_lines)); i = 0
    while i < n and a_lines[i] == b_lines[i]: i += 1
    if i == n and len(a_lines) == len(b_lines): return ""
    s = max(0, i - context); e = min(max(len(a_lines), len(b_lines)), i + context + 1)
    out = ["--- diff snippet (around first mismatch) ---"]
    for k in range(s, e):
        al = a_lines[k] if k < len(a_lines) else ""; bl = b_lines[k] if k < len(b_lines) else ""
        mark = " " if al == bl else "!"
        out.append(f"{mark} L{k+1:04d} | A:{al}")
        out.append(f"{mark} L{k+1:04d} | B:{bl}")
    return "\n".join(out)

def main() -> int:
    ap = argparse.ArgumentParser(description="strict-restore check (no outputs by default)")
    ap.add_argument("root", nargs="?", default=".", help="対象ルート（既定: カレント）")
    ap.add_argument("--verbose", action="store_true", help="処理ファイルを表示")
    ap.add_argument("--show-diff", dest="show_diff", action="store_true", help="不一致時に数行の差分スニペットを表示")
    ap.add_argument("--fix-inplace", action="store_true", help="不一致ファイルを復元内容で同名上書きする（要注意）")
    ap.add_argument("--backup", action="store_true", help="--fix-inplace 時、上書き前に *.bak を保存")
    args = ap.parse_args()

    root = Path(args.root)
    md_files = iter_md_files(root)
    if args.verbose:
        uprint(f"[INFO] target root: {root.resolve()}")
        uprint(f"[INFO] md files   : {len(md_files)}")

    failed = False; fixed = 0

    for p in md_files:
        try:
            src = p.read_text(encoding="utf-8-sig")
        except Exception as e:
            uprint(f"[ERR ] {p}: 読み込みに失敗しました: {e}"); failed = True; continue

        masked, store = extract_protected(src)
        restored = restore_protected(masked, store)

        if restored != src:
            if args.fix_inplace:
                try:
                    if args.backup: shutil.copy2(p, str(p) + ".bak")
                    # LFで保存（改行は任意に合わせてOK）
                    p.write_text(restored, encoding="utf-8", newline="\n")
                    fixed += 1
                    uprint(f"[FIX ] {p}: 復元内容で上書きしました")
                except Exception as e:
                    uprint(f"[ERR ] {p}: 上書きに失敗しました: {e}"); failed = True
            else:
                uprint(f"[NG  ] {p}: 復元結果が原文と一致しません。")
                if args.show_diff: uprint(unified_snippet(src, restored))
                failed = True
        elif args.verbose:
            uprint(f"[OK  ] {p}")

    if args.fix_inplace:
        if failed:
            uprint("\n[NG] 自動修正中にエラーが発生しました。上記メッセージを確認してください。"); return 1
        uprint(f"[OK] fix-inplace 完了。修正ファイル: {fixed}"); return 0

    if failed:
        uprint("\n[NG] “復元＝原文一致”チェックに失敗。未閉じフェンス/表構造/インデント等を見直してください。"); return 1

    uprint("[OK] strict-restore check passed (no outputs)."); return 0

if __name__ == "__main__":
    sys.exit(main())
