# tools/validate_vocab.py
# -*- coding: utf-8 -*-
"""
vocab.json のスキーマ & 整合チェックツール（JSONC対応）
- required / optional / ordered_hint / alias を検査
- canon（= required ∪ optional）と ordered_hint の包含一致
- alias の value は canon 内であること
- 重複や空白語の検知
Exit code: 0=OK, 1=NG
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Set
import sys, json, re

def load_jsonc(path: Path) -> Dict[str, Any]:
    t = path.read_text(encoding="utf-8")
    t = re.sub(r"//.*", "", t)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.DOTALL)
    return json.loads(t)

def uniq(seq: List[str]) -> List[str]:
    seen = set(); out = []
    for s in seq:
        if s not in seen:
            seen.add(s); out.append(s)
    return out

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tools/validate_vocab.py tools/vocab.json")
        return 1
    p = Path(sys.argv[1])
    if not p.exists():
        print(f"[NG ] not found: {p}")
        return 1

    try:
        v = load_jsonc(p)
    except Exception as e:
        print(f"[NG ] JSONC parse error: {e}")
        return 1

    errors: List[str] = []
    warns : List[str] = []

    required = v.get("required", [])
    optional = v.get("optional", [])
    ordered = v.get("ordered_hint", [])
    alias    = v.get("alias", {})

    # 型チェック
    if not isinstance(required, list) or not isinstance(optional, list) or not isinstance(ordered, list) or not isinstance(alias, dict):
        print("[NG ] schema type error: required/optional/ordered_hint must be list, alias must be object")
        return 1

    # 文字列化とトリム
    required = [str(x).strip() for x in required if str(x).strip()]
    optional = [str(x).strip() for x in optional if str(x).strip()]
    ordered  = [str(x).strip() for x in ordered  if str(x).strip()]
    alias    = {str(k).strip(): str(v).strip() for k, v in alias.items() if str(k).strip() and str(v).strip()}

    # 重複検査
    if len(required) != len(set(required)): errors.append("required に重複があります。")
    if len(optional) != len(set(optional)): errors.append("optional に重複があります。")
    if len(ordered ) != len(set(ordered )): errors.append("ordered_hint に重複があります。")

    # canon
    canon_list = uniq(required + optional)
    canon: Set[str] = set(canon_list)

    # aliasのvalueはcanonに含まれること
    bad_alias = [f"{k}->{v}" for k, v in alias.items() if v not in canon]
    if bad_alias:
        errors.append("alias の値が canon（required/optional）に含まれていません: " + ", ".join(bad_alias))

    # ordered_hint は canon をすべて含むべき
    missing_in_order = [w for w in canon_list if w not in set(ordered)]
    if missing_in_order:
        errors.append("ordered_hint に不足がある代表語: " + ", ".join(missing_in_order))

    # ordered_hint にcanon外が混入していないか
    extras_in_order = [w for w in ordered if w not in canon]
    if extras_in_order:
        errors.append("ordered_hint に canon 外の語が含まれています: " + ", ".join(extras_in_order))

    # 推奨：metadata
    for key in ("version", "updated"):
        if key not in v:
            warns.append(f"推奨メタがありません: {key}")

    # レポート
    level = "OK" if not errors else "NG"
    print(f"[{level}] vocab.json check")
    if errors:
        for e in errors: print(" - " + e)
    if warns and not errors:
        print("[WARN] minor advisories:")
        for w in warns: print(" - " + w)

    return 0 if not errors else 1

if __name__ == "__main__":
    raise SystemExit(main())
