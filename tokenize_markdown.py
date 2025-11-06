# tokenize_markdown.py
# -*- coding: utf-8 -*-
"""
Markdownから「壊したくない塊」を退避（トークン化）→ 復元（閲覧用）へ変換する実運用スクリプト

対象:
  - フェンス付きコード: 行頭の可変空白を許容した ```...``` ブロック（言語指定可）
  - インデントコード: 先頭4スペース or タブが連続する塊（※連続2行以上、箇条書き/番号行は除外）
  - Markdown表: ヘッダ |---| を含む表ブロック

出力:
  - {stem}.masked.md  … 本文に {{CODE_xxx}} / {{TABLE_xxx}} のトークンを挿入（機械処理/RAG向け）
  - {stem}.store.json … トークン → 原文ブロックの辞書
  - {stem}.view.md    … 復元済み（人が読む/公開用）。先頭の孤立ダブルクォートを軽微正規化

特徴:
  - 抽出順序は フェンス → インデント → 表（フェンス内混入を防止）
  - トークンの前後に空行を付与（見出し・箇条書きとの結合防止）
  - --strict-restore で復元後が原文と完全一致かをチェック
  - ファイル/ディレクトリ対応（--recursive）

使い方:
    python tokenize_markdown.py manual_002.md
    python tokenize_markdown.py docs --recursive
    python tokenize_markdown.py manual_002.md --strict-restore
"""

from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from typing import Dict, Tuple, List

# ---------- 正規表現パターン ----------

# 1) フェンス付きコード: 行頭の可変空白 + ``` ～ 同様に閉じ``` まで（非貪欲）
FENCED_CODE_RE = re.compile(
    r"(^[ \t]*```[^\n]*\n.*?^[ \t]*```[ \t]*\n?)",
    flags=re.MULTILINE | re.DOTALL
)

# 2) インデントコード:
#    ・先頭4スペース or タブ（※VERBOSEでは空白が無視されるため [ ]{4} と明示）
#    ・箇条書き(- * +)や番号付き(1. )のサブ行を除外
#    ・少なくとも2行以上連続
INDENT_CODE_RE = re.compile(
    r"""
    (                                   # ブロック全体
      (?:
        ^(?:[ ]{4}|\t)                  # 行頭インデント（← 空白は [ ]{4} で明示）
        (?![\*\+\-]\s|\d+\.\s)          # 箇条書き/番号行の除外
        .*\n
      ){2,}                             # 2行以上連続
    )
    """,
    flags=re.MULTILINE | re.VERBOSE
)

# 3) Markdown表: ヘッダ行 + 区切り行 + 1行以上の本体
TABLE_BLOCK_RE = re.compile(
    r"""
    (?P<table>
        ^[ \t]*\|.*\|\s*\n                # ヘッダ行
        [ \t]*\|[ \t]*:?[-=]{3,}.*\|\s*\n # 区切り行 (---, :--- 等)
        (?:[ \t]*\|.*\|\s*\n)+            # 1行以上のデータ
    )
    """,
    flags=re.MULTILINE | re.VERBOSE
)

# ---------- 退避/復元の中核 ----------

def _replace_all(pattern: re.Pattern, kind: str, start_idx: int, s: str, store: Dict[str, str]) -> Tuple[str, int]:
    """
    patternで見つかった塊を順にトークンへ置換し、storeに保存。
    - kind: "CODE" or "TABLE"
    - start_idx: 通し番号の開始
    - トークン前後に空行を付与して見出し等との結合を防止
    """
    idx = start_idx
    out: List[str] = []
    last = 0

    for m in pattern.finditer(s):
        block = m.group(0)
        token = f"{{{{{kind}_{idx:03d}}}}}"
        store[token] = block

        # 直前/直後に改行がなければ付与（安全な余白）
        prefix_need = "" if (last > 0 and s[last-1] == "\n") else "\n"
        suffix_need = "\n" if not (m.end() < len(s) and s[m.end()] == "\n") else ""

        out.append(s[last:m.start()])
        out.append(prefix_need + token + suffix_need)
        last = m.end()
        idx += 1

    out.append(s[last:])
    return "".join(out), idx

def extract_protected(src_text: str) -> Tuple[str, Dict[str, str]]:
    """
    「退避（トークン化）」を実行：
      1) フェンス → 2) インデント → 3) 表
    """
    text = src_text.lstrip("\ufeff")  # 先頭BOM除去
    store: Dict[str, str] = {}
    code_idx = 1
    table_idx = 1

    # 1) フェンスを最優先（内部のインデントは無視される）
    text, code_idx = _replace_all(FENCED_CODE_RE, "CODE", code_idx, text, store)
    # 2) フェンス外インデントコード
    text, code_idx = _replace_all(INDENT_CODE_RE, "CODE", code_idx, text, store)
    # 3) テーブル
    text, table_idx = _replace_all(TABLE_BLOCK_RE, "TABLE", table_idx, text, store)

    return text, store

def restore_protected(masked_text: str, store: Dict[str, str]) -> str:
    """
    「復元」を実行：トークンを辞書の元ブロックへ。
    長いトークンから置換し、偶発的な部分一致を避ける。
    """
    out = masked_text
    for token in sorted(store.keys(), key=len, reverse=True):
        out = out.replace(token, store[token])
    return out

# ---------- 閲覧版の軽微正規化 ----------

# 行頭に孤立して残ったダブルクォートを除去（UNCパス等の見栄え改善）
LEADING_LONE_QUOTE_RE = re.compile(r'^(?P<q>")\s*(?=\\\\|[A-Za-z]:\\)', flags=re.MULTILINE)

def normalize_view(text: str) -> str:
    return LEADING_LONE_QUOTE_RE.sub("", text)

# ---------- 出力ファイル名の生成 ----------

def output_paths(md_path: Path) -> Tuple[Path, Path, Path]:
    stem = md_path.stem
    return (
        md_path.with_name(f"{stem}.masked.md"),
        md_path.with_name(f"{stem}.store.json"),
        md_path.with_name(f"{stem}.view.md"),
    )

# ---------- 1ファイル処理 ----------

def process_file(md_file: Path, strict_restore: bool = False) -> None:
    if not md_file.exists() or md_file.suffix.lower() != ".md":
        print(f"[SKIP] {md_file} はMDではありません。")
        return

    p_mask, p_store, p_view = output_paths(md_file)
    src = md_file.read_text(encoding="utf-8-sig")  # BOM安全読み

    # 退避（トークン化）
    masked, store = extract_protected(src)

    # 復元
    restored = restore_protected(masked, store)

    # 厳格チェック：復元結果が原文と完全一致かどうか
    if strict_restore and restored != src:
        print(f"[ERROR] 復元結果が原文と一致しません: {md_file.name}")
        # 必要ならここで return して処理停止
        # return

    # 出力（閲覧版は軽微正規化を適用）
    p_mask.write_text(masked, encoding="utf-8", newline="\n")
    p_store.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    p_view.write_text(normalize_view(restored), encoding="utf-8", newline="\n")

    print(f"[OK] {md_file.name} -> {p_mask.name}, {p_store.name}, {p_view.name}")

# ---------- ディレクトリ処理 ----------

def process_dir(dir_path: Path, recursive: bool, strict_restore: bool) -> None:
    pattern = "**/*.md" if recursive else "*.md"
    files = sorted(dir_path.glob(pattern))
    if not files:
        print(f"[INFO] 対象MDが見つかりません: {dir_path}")
        return
    for f in files:
        process_file(f, strict_restore=strict_restore)

# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(description="Markdownを退避（トークン化）→復元（閲覧用）へ変換")
    ap.add_argument("path", help="入力MDファイル or ディレクトリ")
    ap.add_argument("--recursive", action="store_true", help="ディレクトリを再帰処理")
    ap.add_argument("--strict-restore", action="store_true", help="復元後の原文一致チェックを有効化")
    args = ap.parse_args()

    p = Path(args.path)
    if p.is_file():
        process_file(p, strict_restore=args.strict_restore)
    elif p.is_dir():
        process_dir(p, recursive=args.recursive, strict_restore=args.strict_restore)
    else:
        print(f"[ERROR] 入力が見つかりません: {p}")

if __name__ == "__main__":
    main()
