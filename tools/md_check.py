# tools/md_check.py
# -*- coding: utf-8 -*-
"""
Unified Markdown Checker (1〜5)
  (1) BOM check
  (2) Fence check (code block ``` ... ```; unclosed detection)
  (3) Meta parsing (title/summary/tags) with simple heuristics
  (4) Headings check (## ...) with vocab (required/optional/ordered_hint/alias)
  (5) Notation normalization:
        - Built-in: fullwidth 'Ｑ' -> 'Q' ONLY OUTSIDE exclusion ranges
        - CSV rules: literal/regex, scope=outside|everywhere, flags=IMSX
        - IMPORTANT: <...> (angle tags) are ALWAYS EXCLUDED from conversion
Options:
  --no-bom / --no-fence / --no-meta / --no-headings / --no-notation
  --vocab PATH                # JSON/JSONC vocab
  --summary-min INT --summary-max INT --tags-min INT --tags-max INT
  --fix-notation              # apply notation fix; .bak backup
  --rules PATH                # CSV rules (enabled,kind,pattern,replacement,scope,flags,note)
  --report-json
Exit code:
  0 : OK/WARN/FIX only
  1 : NG included (read/write error etc.)
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Iterable, Dict, Any, Optional
import argparse, csv, glob, json, re, shutil, sys

# ========== Common IO / ranges ==========
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

def fence_ranges(text: str) -> Tuple[List[Tuple[int,int]], bool]:
    """
    return (ranges, unclosed)
      ranges : [start,end) offsets of fenced code blocks
      unclosed : True if unclosed fence exists
    """
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
    return rs, in_fence  # in_fence=True → 未閉じ

def angle_tag_ranges(text: str) -> List[Tuple[int,int]]:
    rs: List[Tuple[int,int]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "<":
            j = text.find(">", i+1)
            if j == -1:
                i += 1
                continue
            rs.append((i, j+1))
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

# --- new: separate masks ---
def angle_exclusion_mask(text: str) -> bytearray:
    return build_exclusion_mask(text, angle_tag_ranges(text))

def fence_exclusion_mask(text: str) -> bytearray:
    fr, _ = fence_ranges(text)
    return build_exclusion_mask(text, fr)

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

# ========== (1) BOM check ==========
def check_bom(path: Path) -> Dict[str,Any]:
    data = path.read_bytes()
    has_bom = data.startswith(BOM)
    return {"file": path.name, "level": "OK" if not has_bom else "WARN",
            "bom": "absent" if not has_bom else "present",
            "issues": (["BOMが付与されています。可能ならBOMなしUTF-8推奨。"] if has_bom else [])}

# ========== (2) Fence check ==========
def check_fence(text: str, fname: str) -> Dict[str,Any]:
    ranges, unclosed = fence_ranges(text)
    level = "OK" if not unclosed else "NG"
    issues = []
    if unclosed:
        issues.append("未閉じのコードフェンス（```）があります。閉じ忘れを確認してください。")
    return {"file": fname, "level": level, "fence_blocks": len(ranges), "unclosed": unclosed, "issues": issues}

# ========== (3) Meta parsing ==========
META_H2 = r"^##\s*メタ情報（概要）\s*$"

def extract_meta_block(txt: str) -> Tuple[str, int, int]:
    lines = txt.split("\n")
    start = -1
    for i, ln in enumerate(lines):
        if re.match(META_H2, ln):
            start = i; break
    if start < 0: return "", -1, -1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^##\s+", lines[j]):  # next H2
            end = j; break
    block = "\n".join(lines[start:end])
    return block, start+1, end

def _get_field(block: str, label: str) -> str:
    m = re.search(rf"^-+\s*{re.escape(label)}\s*:\s*(.+)$", block, flags=re.MULTILINE)
    return (m.group(1).strip() if m else "")

def _parse_tags(block: str) -> List[str]:
    line = _get_field(block, "カテゴリ/タグ")
    if not line: return []
    parts = re.split(r"\s*/\s*", line, maxsplit=1)
    tag_part = parts[1] if len(parts) > 1 else ""
    tag_part = tag_part.replace("、", ",").replace("，", ",").replace("　", " ")
    raw = re.split(r"[, ]+", tag_part.strip())
    return [t for t in raw if t]

def _is_single_sentence(s: str) -> bool:
    if "\n" in s: return False
    t = re.sub(r"（[^）]*）", "", s)
    t = re.sub(r"\([^)]*\)", "", t)
    t = re.sub(r"【[^】]*】", "", t)
    t = re.sub(r"\bv?\d+(?:\.\d+){1,3}\b", lambda m: m.group(0).replace(".", ""), t)
    marks = re.findall(r"[。．.!?！？]", t)
    return len(marks) <= 1

def check_meta(text: str, fname: str, smin: int, smax: int, tmin: int, tmax: int) -> Dict[str,Any]:
    block, sline, eline = extract_meta_block(text)
    if not block:
        return {"file": fname, "level": "NG", "issues": ["メタ見出し（## メタ情報（概要））が見つかりません。"]}
    title = _get_field(block, "タイトル")
    summary = _get_field(block, "要約")
    tags = _parse_tags(block)

    level = "OK"
    issues: List[str] = []
    if not title:
        level = "NG"; issues.append("タイトルが空です。")
    if not summary:
        level = "NG"; issues.append("要約が見つかりません。")
    else:
        n = len(summary)
        if n < smin or n > smax:
            if level != "NG": level = "WARN"
            issues.append(f"要約の文字数が範囲外です: {n}文字（{smin}〜{smax}推奨）")
        if not _is_single_sentence(summary):
            if level != "NG": level = "WARN"
            issues.append("要約が1文ではない可能性があります。")

    uniq = list(dict.fromkeys(tags))
    if len(uniq) < tmin or len(uniq) > tmax:
        if level != "NG": level = "WARN"
        issues.append(f"タグ語数が推奨範囲外です: {len(uniq)}語（{tmin}〜{tmax}推奨）")

    return {
        "file": fname, "level": level, "issues": issues,
        "title": bool(title), "summary_len": len(summary) if summary else 0,
        "tags_count": len(uniq)
    }

# ========== (4) Headings check ==========
DEFAULT_VOCAB = {
    "required": ["注意（重要）","体裁","確認","変更履歴"],
    "optional": ["指示書","刷本","例外","関連（参考）","よくある質問（FAQ）","索引","目次","原稿整理","目的","範囲","定義","前提","手順","根拠（監査）","適用期間","連絡先"],
    "ordered_hint": ["目的","範囲","定義","前提","手順","指示書","刷本","体裁","注意（重要）","確認","例外","関連（参考）","索引","目次","原稿整理","変更履歴","連絡先","根拠（監査）"],
    "alias": {"注意事項":"注意（重要）","See also":"関連（参考）","参照":"関連（参考）","改訂履歴":"変更履歴","使い方":"前提"}
}

def _load_vocab(path: Optional[Path]) -> Dict[str,Any]:
    if not path:
        v = DEFAULT_VOCAB.copy()
    else:
        # JSONC: strip // and /*...*/
        txt = Path(path).read_text(encoding="utf-8")
        txt = re.sub(r"//.*", "", txt)
        txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.DOTALL)
        v = json.loads(txt)
    canon = list(dict.fromkeys(v.get("required", []) + v.get("optional", [])))
    v["canon"] = canon
    return v

def _extract_h2(txt: str) -> List[str]:
    out = []
    for m in re.finditer(r"^##\s+(.+)$", txt, flags=re.MULTILINE):
        h = m.group(1).strip()
        if re.match(META_H2, f"## {h}"):  # skip meta H2
            continue
        out.append(h)
    return out

def _normalize_heading(h: str, vocab: Dict[str,Any]) -> str:
    s = re.sub(r"\s+", " ", h.strip())
    alias = vocab.get("alias", {})
    return alias.get(s, s)

def _order_score(heads: List[str], hint: List[str]) -> int:
    pos = {h:i for i,h in enumerate(hint)}
    idx = [pos[h] for h in heads if h in pos]
    inv = 0
    for i in range(len(idx)):
        for j in range(i+1, len(idx)):
            if idx[i] > idx[j]: inv += 1
    return inv

def check_headings(text: str, fname: str, vocab: Dict[str,Any]) -> Dict[str,Any]:
    raw = _extract_h2(text)
    norm = [_normalize_heading(h, vocab) for h in raw]
    present = set(norm)
    required = set(vocab.get("required",[]))
    canon = set(vocab.get("canon",[]))

    missing = sorted(list(required - present))
    extras = [h for h in norm if h not in canon]
    score = _order_score(norm, vocab.get("ordered_hint", []))
    level = "OK"
    issues: List[str] = []
    if missing:
        level = "NG"; issues.append("必須見出しの不足: " + ", ".join(missing))
    if extras:
        if level != "NG": level = "WARN"
        issues.append("代表語以外の見出しあり: " + ", ".join(extras))
    if score > 0:
        if level != "NG": level = "WARN"
        issues.append(f"順序のズレ（逆転数={score}）: 推奨順に概ね合わせてください。")
    return {"file": fname, "level": level, "issues": issues, "headings": norm, "order_score": score}

# ========== (5) Notation (builtin + CSV with angle lock) ==========
@dataclass
class Rule:
    enabled: bool
    kind: str           # literal | regex
    pattern: str
    replacement: str
    scope: str          # outside | everywhere
    flags: int
    note: str = ""

    def compiled(self):
        return re.compile(self.pattern, self.flags) if self.kind == "regex" else None

_FLAG_MAP = {"I": re.IGNORECASE, "M": re.MULTILINE, "S": re.DOTALL, "X": re.VERBOSE}
def _parse_flags(s: str | None) -> int:
    if not s: return 0
    f = 0
    for ch in s.strip().upper(): f |= _FLAG_MAP.get(ch, 0)
    return f

def _load_rules_csv(csv_path: Path) -> List[Rule]:
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
                enabled=enabled, kind=kind,
                pattern=row["pattern"] or "", replacement=row["replacement"] or "",
                scope=scope, flags=_parse_flags(row.get("flags")), note=row.get("note","")
            ))
    return [r for r in rules if r.enabled]

@dataclass
class Hit:
    start: int; end: int; before: str; after: str; rule_idx: int; source: str

def _apply_literal(text: str, pattern: str, repl: str,
                   angle_mask: Optional[bytearray],
                   fence_mask: Optional[bytearray],
                   outside_only: bool, source: str, idx: int) -> Tuple[str,List[Hit]]:
    if not pattern: return text, []
    hits: List[Hit] = []; out = []; i = 0; n = len(text); lp = len(pattern)
    while i < n:
        j = text.find(pattern, i)
        if j < 0:
            out.append(text[i:]); break
        # angle: ALWAYS excluded
        if angle_mask is not None and any(angle_mask[k] for k in range(j, j+lp)):
            out.append(text[i:j+lp]); i = j + lp; continue
        # fence: excluded only when outside_only
        if outside_only and fence_mask is not None and any(fence_mask[k] for k in range(j, j+lp)):
            out.append(text[i:j+lp]); i = j + lp; continue
        out.append(text[i:j]); out.append(repl)
        hits.append(Hit(j, j+lp, text[j:j+lp], repl, idx, source))
        i = j + lp
    return "".join(out), hits

def _apply_regex(text: str, rx: re.Pattern, repl: str,
                 angle_mask: Optional[bytearray],
                 fence_mask: Optional[bytearray],
                 outside_only: bool, source: str, idx: int) -> Tuple[str,List[Hit]]:
    hits: List[Hit] = []; out = []; last = 0
    for m in rx.finditer(text):
        s, e = m.start(), m.end()
        # angle: ALWAYS excluded
        if angle_mask is not None and any(angle_mask[k] for k in range(s, e)):
            continue
        # fence: excluded only when outside_only
        if outside_only and fence_mask is not None and any(fence_mask[k] for k in range(s, e)):
            continue
        before = text[s:e]; after = m.expand(repl)
        out.append(text[last:s]); out.append(after)
        hits.append(Hit(s, e, before, after, idx, source))
        last = e
    out.append(text[last:])
    return "".join(out), hits

def apply_rules(text: str, rules: List[Rule],
                angle_mask: Optional[bytearray],
                fence_mask: Optional[bytearray],
                source: str) -> Tuple[str,List[Hit]]:
    cur = text; all_hits: List[Hit] = []
    for idx, r in enumerate(rules):
        outside_only = (r.scope == "outside")
        if r.kind == "literal":
            cur, hits = _apply_literal(cur, r.pattern, r.replacement,
                                       angle_mask, fence_mask,
                                       outside_only, source, idx)
        else:
            rx = r.compiled()
            cur, hits = _apply_regex(cur, rx, r.replacement,
                                     angle_mask, fence_mask,
                                     outside_only, source, idx)
        all_hits.extend(hits)
    return cur, all_hits

def notation_check_and_fix(path: Path, fix: bool, rules_csv: Optional[Path]) -> Dict[str,Any]:
    txt = read_text_normalized(path)
    angle_mask = angle_exclusion_mask(txt)   # ALWAYS exclude <...>
    fence_mask = fence_exclusion_mask(txt)   # exclude ```...``` only when outside_only
    # builtin (outside only): Ｑ -> Q (角括弧常時除外＋フェンス外のみ)
    builtin = [Rule(True,"literal","Ｑ","Q","outside",0,"builtin fullwidthQ -> Q")]
    cur, hits1 = apply_rules(txt, builtin, angle_mask, fence_mask, "builtin")
    # CSV
    hits2: List[Hit] = []
    if rules_csv:
        csv_rules = _load_rules_csv(rules_csv)
        cur, hits2 = apply_rules(cur, csv_rules, angle_mask, fence_mask, "csv")
    hits = hits1 + hits2
    if not hits:
        return {"file": path.name, "level": "OK", "hits": 0}
    if not fix:
        samples = [{"src": h.source, "before": h.before, "after": h.after,
                    "start": h.start, "end": h.end, "rule_idx": h.rule_idx}
                   for h in hits[:20]]
        return {"file": path.name, "level": "HIT", "hits": len(hits), "samples": samples}
    try:
        bk = backup_path(path)
        shutil.copy2(path, bk)
        path.write_text(cur, encoding="utf-8", newline="\n")
        return {"file": path.name, "level": "FIX", "hits": len(hits), "backup": bk.name}
    except Exception as e:
        return {"file": path.name, "level": "NG", "error": f"write error: {e}"}

# ========== Main ==========
def main() -> int:
    ap = argparse.ArgumentParser(description="Unified Markdown Checker (1-5)")
    ap.add_argument("targets", nargs="+", help="対象MD（ファイル/フォルダ/ワイルドカード可）")
    # toggles
    ap.add_argument("--no-bom", action="store_true")
    ap.add_argument("--no-fence", action="store_true")
    ap.add_argument("--no-meta", action="store_true")
    ap.add_argument("--no-headings", action="store_true")
    ap.add_argument("--no-notation", action="store_true")
    # meta params
    ap.add_argument("--summary-min", type=int, default=50)
    ap.add_argument("--summary-max", type=int, default=120)
    ap.add_argument("--tags-min", type=int, default=5)
    ap.add_argument("--tags-max", type=int, default=10)
    # headings
    ap.add_argument("--vocab", type=Path, help="語彙JSON/JSONC（required/optional/ordered_hint/alias）")
    # notation
    ap.add_argument("--fix-notation", action="store_true")
    ap.add_argument("--rules", type=Path, help="表記ゆれ拡張CSV（enabled,kind,pattern,replacement,scope,flags,note）")
    # output
    ap.add_argument("--report-json", action="store_true")
    args = ap.parse_args()

    files = expand_targets(args.targets)
    if not files:
        print("[INFO] 対象が見つかりません。"); return 0

    vocab = None
    if not args.no_headings:
        try:
            vocab = _load_vocab(args.vocab)  # Noneならデフォルト
        except Exception as e:
            print(f"[NG ] 語彙ロード失敗: {e}")
            return 1

    any_ng = False
    reports: Dict[str, Dict[str,Any]] = {}

    for f in files:
        try:
            txt = read_text_normalized(f)
        except Exception as e:
            any_ng = True
            print(f"{f.name:30s} : NG   | read error: {e}")
            reports[f.name] = {"file": f.name, "level": "NG", "error": f"read error: {e}"}
            continue

        print(f"\n[FILE] {f.name}")

        # (1) BOM
        if not args.no_bom:
            r1 = check_bom(f)
            print(f"  (1) BOM        : {r1['level']:4s} | {r1.get('bom')}")
            for i in r1.get("issues", []): print(f"      -> {i}")
            any_ng = any_ng or (r1["level"] == "NG")
            reports.setdefault(f.name, {})["bom"] = r1

        # (2) Fence
        if not args.no_fence:
            r2 = check_fence(txt, f.name)
            print(f"  (2) Fence      : {r2['level']:4s} | blocks={r2['fence_blocks']} unclosed={r2['unclosed']}")
            for i in r2.get("issues", []): print(f"      -> {i}")
            any_ng = any_ng or (r2["level"] == "NG")
            reports.setdefault(f.name, {})["fence"] = r2

        # (3) Meta
        if not args.no_meta:
            r3 = check_meta(txt, f.name, args.summary_min, args.summary_max, args.tags_min, args.tags_max)
            print(f"  (3) Meta       : {r3['level']:4s} | title={'OK' if r3.get('title') else 'NG'} summary={r3.get('summary_len')} tags={r3.get('tags_count')}")
            for i in r3.get("issues", []): print(f"      -> {i}")
            any_ng = any_ng or (r3["level"] == "NG")
            reports.setdefault(f.name, {})["meta"] = r3

        # (4) Headings
        if not args.no_headings:
            r4 = check_headings(txt, f.name, vocab)
            print(f"  (4) Headings   : {r4['level']:4s} | h={len(r4.get('headings', []))} inv={r4.get('order_score')}")
            for i in r4.get("issues", []): print(f"      -> {i}")
            any_ng = any_ng or (r4["level"] == "NG")
            reports.setdefault(f.name, {})["headings"] = r4

        # (5) Notation
        if not args.no_notation:
            r5 = notation_check_and_fix(f, args.fix_notation, args.rules)
            print(f"  (5) Notation   : {r5['level']:4s} | hits={r5.get('hits',0)}" + (f" backup={r5.get('backup')}" if r5.get("backup") else ""))
            if r5["level"] == "NG": any_ng = True
            reports.setdefault(f.name, {})["notation"] = r5

    if args.report_json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))

    return 1 if any_ng else 0

if __name__ == "__main__":
    raise SystemExit(main())
