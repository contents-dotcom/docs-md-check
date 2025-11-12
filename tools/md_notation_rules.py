# tools/md_notation_rules.py
# -*- coding: utf-8 -*-
"""
Markdown Notation Normalizer (Unified)
- Code fences (``` ... ```) and angle tags (<...>) are exclusion ranges.
- Built-in rule: convert full-width 'Ｑ' to 'Q' only OUTSIDE exclusion ranges.
- Extensible via CSV rules: literal/regex, scope=outside|everywhere, flags=IMSX.

Usage examples:
  python tools/md_notation_rules.py docs/*.md
  python tools/md_notation_rules.py MD_KH.md --rules tools/rules.csv
  python tools/md_notation_rules.py docs/ --rules tools/rules.csv --fix --report-json
  python tools/md_notation_rules.py MD_KH.md --no-default-q
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Iterable, Dict, Any, Optional
import argparse, csv, glob, json, re, shutil, sys

# ==== Common helpers =========================================================
BOM = b"\xEF\xBB\xBF"
FENCE = "```"

def read_text_normalized(p: Path) -> str:
    b = p.read_bytes()
    if b.startswith(BOM):
        b = b[3:]
    t = b.decode("utf-8", errors="replace")
    return t.replace("\r\n", "\n").replace("\r", "\n")

def _line_offsets(lines: List[str]) -> List[int]:
    offs, o = [], 0
    for i, ln in enumerate(lines):
        offs.append(o)
        o += len(ln) + (0 if i == len(lines)-1 else 1)
    return offs

def fence_ranges(text: str) -> List[Tuple[int,int]]:
    lines = text.split("\n")
    offs  = _line_offsets(lines)
    rs: List[Tuple[int,int]] = []
    in_fence = False
    start_line = -1
    for i, ln in enumerate(lines):
        if ln.startswith(FENCE):
            if not in_fence:
                in_fence = True
                start_line = i
            else:
                rs.append((offs[start_line], offs[i] + len(lines[i])))
                in_fence = False
                start_line = -1
    return rs

def angle_tag_ranges(text: str) -> List[Tuple[int,int]]:
    rs: List[Tuple[int,int]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "<":
            j = text.find(">", i+1)
            if j == -1:
                i += 1  # 未閉じは除外しない（別チェックで扱う想定）
                continue
            rs.append((i, j+1))  # '>' を含む
            i = j + 1
        else:
            i += 1
    return rs

def build_exclusion_mask(text: str, ranges: Iterable[Tuple[int,int]]) -> bytearray:
    m = bytearray(len(text))
    for s, e in ranges:
        s = max(0, min(len(text), s)); e = max(0, min(len(text), e))
        for k in range(s, e): m[k] = 1
    return m

def exclusion_mask_for(text: str) -> bytearray:
    ex = fence_ranges(text) + angle_tag_ranges(text)
    return build_exclusion_mask(text, ex)

def backup_path(p: Path) -> Path:
    cand = p.with_suffix(p.suffix + ".bak")
    if not cand.exists(): return cand
    i = 1
    while True:
        alt = p.with_suffix(p.suffix + f".bak.{i}")
        if not alt.exists(): return alt
        i += 1

def expand_targets(patterns: List[str]) -> List[Path]:
    out: List[Path] = []
    for pat in patterns:
        p = Path(pat)
        if p.is_dir():
            out.extend(p.rglob("*.md"))
        else:
            matched = [Path(s) for s in glob.glob(pat)]
            if matched:
                out.extend(matched)
            elif p.exists():
                out.append(p)
    return sorted({x.resolve() for x in out if x.is_file()})

# ==== Rule model & CSV loader ===============================================
@dataclass
class Rule:
    enabled: bool
    kind: str           # "literal" | "regex"
    pattern: str
    replacement: str
    scope: str          # "outside" | "everywhere"
    flags: int
    note: str = ""

    def compiled(self):
        return re.compile(self.pattern, self.flags) if self.kind == "regex" else None

_FLAG_MAP = {"I": re.IGNORECASE, "M": re.MULTILINE, "S": re.DOTALL, "X": re.VERBOSE}

def parse_flags(s: str | None) -> int:
    if not s: return 0
    f = 0
    for ch in s.strip().upper():
        f |= _FLAG_MAP.get(ch, 0)
    return f

def load_rules_csv(csv_path: Path) -> List[Rule]:
    rules: List[Rule] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"enabled","kind","pattern","replacement","scope","flags","note"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSVヘッダ不足: {missing}")
        for i, row in enumerate(reader, 2):
            enabled = str(row["enabled"]).strip().lower() in {"1","true","yes","on"}
            kind    = (row["kind"] or "").strip().lower()
            scope   = (row["scope"] or "outside").strip().lower()
            if kind not in {"literal","regex"}:
                raise ValueError(f"{csv_path.name}:{i} 行: kindは literal か regex")
            if scope not in {"outside","everywhere"}:
                raise ValueError(f"{csv_path.name}:{i} 行: scopeは outside か everywhere")
            rules.append(Rule(
                enabled=enabled,
                kind=kind,
                pattern=row["pattern"] or "",
                replacement=row["replacement"] or "",
                scope=scope,
                flags=parse_flags(row.get("flags")),
                note=row.get("note",""),
            ))
    return [r for r in rules if r.enabled]

# ==== Rule application =======================================================
@dataclass
class Hit:
    start: int
    end: int
    before: str
    after:  str
    rule_idx: int
    source:  str  # "builtin" or "csv"

def _apply_literal(text: str, pattern: str, replacement: str, mask: Optional[bytearray], outside_only: bool) -> Tuple[str, List[Hit]]:
    if not pattern:
        return text, []
    hits: List[Hit] = []
    i, n = 0, len(text)
    lp = len(pattern)
    out = []
    while i < n:
        j = text.find(pattern, i)
        if j < 0:
            out.append(text[i:]); break
        if outside_only and mask is not None:
            if any(mask[k] for k in range(j, j+lp)):
                out.append(text[i:j+lp]); i = j + lp; continue
        out.append(text[i:j]); out.append(replacement)
        hits.append(Hit(j, j+lp, text[j:j+lp], replacement, -1, "builtin"))
        i = j + lp
    return "".join(out), hits

def _apply_regex(text: str, rx: re.Pattern, replacement: str, mask: Optional[bytearray], outside_only: bool) -> Tuple[str, List[Hit]]:
    hits: List[Hit] = []
    out = []; last = 0
    for m in rx.finditer(text):
        s, e = m.start(), m.end()
        if outside_only and mask is not None and any(mask[k] for k in range(s, e)):
            continue
        before = text[s:e]
        after  = m.expand(replacement)
        out.append(text[last:s]); out.append(after)
        hits.append(Hit(s, e, before, after, -1, "csv"))
        last = e
    out.append(text[last:])
    return "".join(out), hits

def apply_rules(text: str, rules: List[Rule], mask: Optional[bytearray]) -> Tuple[str, List[Hit]]:
    cur = text
    all_hits: List[Hit] = []
    for idx, r in enumerate(rules):
        if r.kind == "literal":
            cur, hits = _apply_literal(cur, r.pattern, r.replacement, mask if r.scope=="outside" else None, r.scope=="outside")
        else:
            rx = r.compiled()
            cur, hits = _apply_regex(cur, rx, r.replacement, mask if r.scope=="outside" else None, r.scope=="outside")
        for h in hits: h.rule_idx = idx
        all_hits.extend(hits)
    return cur, all_hits

# ==== CLI main ==============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="Markdown Notation Normalizer (Unified)")
    ap.add_argument("targets", nargs="+", help="対象MD（ファイル/フォルダ/ワイルドカード可）")
    ap.add_argument("--rules", type=Path, help="ルールCSV（UTF-8, header required）")
    ap.add_argument("--fix", action="store_true", help="上書き（.bak退避）")
    ap.add_argument("--report-json", action="store_true", help="結果をJSONで標準出力")
    ap.add_argument("--no-default-q", action="store_true", help="内蔵の『角括弧外Ｑ→Q』を無効化")
    args = ap.parse_args()

    # 1) ルールロード
    csv_rules: List[Rule] = []
    if args.rules:
        try:
            csv_rules = load_rules_csv(args.rules)
        except Exception as e:
            print(f"[NG ] ルール読込エラー: {e}")
            return 1

    # 2) デフォルトＱ→Q（角括弧外のみ）
    builtin_rules: List[Rule] = []
    if not args.no_default_q:
        builtin_rules.append(Rule(
            enabled=True, kind="literal",
            pattern="Ｑ", replacement="Q",
            scope="outside", flags=0, note="角括弧/フェンス外のみ：全角Ｑ→Q"
        ))

    files = expand_targets(args.targets)
    if not files:
        print("[INFO] 対象が見つかりません。"); return 0

    reports: List[Dict[str,Any]] = []
    any_ng = False

    print("[RUN ] md-notation-rules")
    print(f"       builtin: {'ON' if builtin_rules else 'OFF'} / csv: {len(csv_rules)} rules")
    for f in files:
        # 読み込み
        try:
            txt = read_text_normalized(f)
        except Exception as e:
            any_ng = True
            reports.append({"file": f.name, "level": "NG", "error": f"read error: {e}"})
            print(f" - {f.name:30s} : NG   | read error: {e}")
            continue

        # 除外マスク
        mask = exclusion_mask_for(txt)

        # 適用順：内蔵 → CSV
        cur, hits1 = apply_rules(txt, builtin_rules, mask) if builtin_rules else (txt, [])
        cur, hits2 = apply_rules(cur, csv_rules, mask) if csv_rules else (cur, [])

        hits = hits1 + hits2
        if not hits:
            reports.append({"file": f.name, "level": "OK", "hits": 0})
            print(f" - {f.name:30s} : OK   | hits=0")
            continue

        if args.fix:
            try:
                bk = backup_path(f)
                shutil.copy2(f, bk)
                f.write_text(cur, encoding="utf-8", newline="\n")
                reports.append({"file": f.name, "level": "FIX", "hits": len(hits), "backup": bk.name})
                print(f" - {f.name:30s} : FIX  | hits={len(hits)} backup={bk.name}")
            except Exception as e:
                any_ng = True
                reports.append({"file": f.name, "level": "NG", "error": f"write error: {e}"})
                print(f" - {f.name:30s} : NG   | write error: {e}")
        else:
            # dry-run
            samples = [{"start": h.start, "end": h.end, "before": h.before, "after": h.after, "rule_idx": h.rule_idx, "src": h.source} for h in hits[:20]]
            reports.append({"file": f.name, "level": "HIT", "hits": len(hits), "samples": samples})
            print(f" - {f.name:30s} : HIT  | hits={len(hits)} (dry-run)")

    if args.report_json:
        print(json.dumps({"reports": reports}, ensure_ascii=False, indent=2))

    return 1 if any_ng else 0

if __name__ == "__main__":
    raise SystemExit(main())
