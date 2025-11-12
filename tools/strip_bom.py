# tools/check_1_bom.py
# -*- coding: utf-8 -*-
"""
【目的】
  - UTF-8のBOM(0xEF 0xBB 0xBF)を検出し、レポートします。
  - --fix 指定時は、BOMを安全に除去し、<同名>.bak を自動退避した上で上書きします。
  - その後、UTF-8でテキストとして読めるか(デコード可能か)も簡易検査します。

【判定】
  - OK   : BOMなし + UTF-8デコードOK
  - WARN : BOMあり（--fixなし） ※UTF-8デコードOK前提
  - FIX  : BOMを除去して上書き（--fix時）
  - NG   : ファイル読込不可 / UTF-8デコード不可

【終了コード】
  - 0 : OK / WARN / FIX のみ
  - 1 : NG を1件以上含む

【使い方例】
  - 乾式実行（確認のみ）:  python tools/check_1_bom.py docs/*.md
  - 自動修正（BOM除去） :  python tools/check_1_bom.py docs/*.md --fix
  - 何もマッチしない場合:  Windowsはシェル展開されない場合があるため、powershell か python側のglobをご利用ください。

"""

from __future__ import annotations
import argparse
from pathlib import Path
import sys
import shutil
import glob

BOM = b"\xEF\xBB\xBF"

def backup_path(p: Path) -> Path:
    """<同名>.bak があれば連番を付けて退避先を作る"""
    cand = p.with_suffix(p.suffix + ".bak")
    if not cand.exists():
        return cand
    i = 1
    while True:
        alt = p.with_suffix(p.suffix + f".bak.{i}")
        if not alt.exists():
            return alt
        i += 1

def check_and_fix_bom(path: Path, do_fix: bool) -> tuple[str, str]:
    """
    戻り値: (level, detail)
      level: "OK" | "WARN" | "FIX" | "NG"
      detail: 人向けメッセージ
    """
    try:
        data = path.read_bytes()
    except Exception as e:
        return "NG", f"read error: {e}"

    had_bom = data.startswith(BOM)

    # UTF-8デコード検査（BOMはあってもなくてもいったん除去して試す）
    try:
        if had_bom:
            data[BOM.__len__():].decode("utf-8")
        else:
            data.decode("utf-8")
    except Exception as e:
        # ここでNG扱い（UTF-8以外の可能性）
        return "NG", f"utf-8 decode error: {e}"

    if not had_bom:
        return "OK", "BOMなし / utf-8 OK"

    # had_bom = True
    if not do_fix:
        return "WARN", "BOMあり（--fix未指定）/ utf-8 OK"

    # --fix: 退避してBOM除去で上書き
    try:
        bk = backup_path(path)
        shutil.copy2(path, bk)
        path.write_bytes(data[len(BOM):])  # 中身はそのまま、BOMだけ除去
        return "FIX", f"BOMを除去して上書き / 旧版退避: {bk.name}"
    except Exception as e:
        return "NG", f"fix error: {e}"

def expand_targets(patterns: list[str]) -> list[Path]:
    """ワイルドカード/ディレクトリ混在を解決して最終的なファイルリストにする"""
    out: list[Path] = []
    for pat in patterns:
        p = Path(pat)
        if p.is_dir():
            out.extend(Path(p).rglob("*.md"))
        else:
            # Windowsのcmdはワイルドカードを展開しないことがあるためpython側でglob
            matched = [Path(s) for s in glob.glob(pat)]
            if matched:
                out.extend(matched)
            elif p.exists():
                out.append(p)
    # 重複除去＆ソート
    uniq = sorted(set([x.resolve() for x in out if x.is_file()]))
    return uniq

def main() -> int:
    ap = argparse.ArgumentParser(description="(1) BOMチェック & 任意でBOM除去（.bak退避）")
    ap.add_argument("targets", nargs="+", help="検査対象（ファイル/フォルダ/ワイルドカード可）")
    ap.add_argument("--fix", action="store_true", help="BOMがあれば除去して上書き（.bak退避）")
    args = ap.parse_args()

    files = expand_targets(args.targets)
    if not files:
        print("[INFO] 対象が見つかりませんでした。パス/ワイルドカードを確認してください。")
        return 0

    has_ng = False
    print("[CHECK] 1. BOM検査（utf-8確認込み）")
    for f in files:
        level, detail = check_and_fix_bom(f, args.fix)
        print(f" - {f.name:30s} : {level:4s} | {detail}")
        if level == "NG":
            has_ng = True

    return 1 if has_ng else 0

if __name__ == "__main__":
    sys.exit(main())
