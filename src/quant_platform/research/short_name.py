"""A rule's short name (2026-11-15; D-046 第 9 項、介面檢查 U4：規則名稱一行 60～120 字，表格一列擠成 7 行).

The full name states every setting ("深度學習前一成・不買技術機器人最不看好的五分之一：前 10 名、同產業最多 3 成、每週、前 30 名內
續抱、看成本才換股、只管買進｜持有：持有 20 天"); a table needs what tells one rule from the next at a glance: what it
ranks by, how many it buys, how it holds, whether it cuts in a falling market. ``short`` makes that from the name — the
name is what every page already has — and the rule's page shows the full name and every setting. The number ("#7339")
stays the identity; two rules may share a short name.
"""

from __future__ import annotations

import re

PREFIXES = ("掃描", "新資料", "組合搜尋", "AI", "技術因子動能", "因子動能")
_TOP = re.compile(r"(?:前|挑)\s*(\d+)\s*名(?!內)|最低的\s*(\d+)\s*檔")
_HOLD = (("月線上續抱", "月線上續抱"), ("固定持有 20 天", "抱 20 天"), ("持有 20 天", "抱 20 天"),
         ("不設最短持有", "隨時換股"), ("不設最短持", "隨時換股"))
_GUARD = (("依大盤波動減碼", "跌市依波動減碼"), ("改選跌市穩的股票", "跌市換穩的股票"), ("減碼一半", "跌市減碼一半"))
LIMIT = 20


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip("＋・、（ ") + "…"


def _core(head: str) -> str:
    first, colon, rest = head.partition("：")
    if colon and first.strip() in PREFIXES:
        if first.strip() == "組合搜尋":
            members = re.findall(r"#\d+", rest)
            return "組合 " + "＋".join(members[:3]) + ("…" if len(members) > 3 else "") if members else _cut("組合 " + rest, LIMIT)
        first = rest.partition("：")[0] if "：" in rest else rest
        first = re.split(r"、前\s*\d+\s*名|、同產業", first)[0]
    first = re.sub(r"（#\d+ 的設定.*$", "", first)                       # "（#6090 的設定、前 10 名）…" is settings
    first = re.sub(r"（[^）]{9,}）", "", first).strip(" ・＋")            # a long aside in brackets is an explanation
    parts = [part.strip() for part in first.split("＋") if part.strip()]
    if len(parts) > 2:
        return _cut("＋".join(parts[:2]), LIMIT - 4) + f" 等 {len(parts)} 個"
    return _cut("＋".join(parts) or first, LIMIT)


def short(name: str | None) -> str:
    """"深度學習前一成・不買技術機器…・前 10 名・抱 20 天" from the full name; the name itself when it is already short."""
    text = re.sub(r"^每天\s+", "", str(name or "").strip())
    if len(text) <= 24:
        return text
    head, _bar, hold = text.partition("｜持有：")
    bits = [_core(head)]
    top = _TOP.search(head)
    if top and "名" not in bits[0]:
        bits.append(f"前 {top.group(1)} 名" if top.group(1) else f"最低 {top.group(2)} 檔")
    where = hold or head
    bits += [label for needle, label in _HOLD if needle in where][:1]
    bits += [label for needle, label in _GUARD if needle in text][:1]
    return "・".join(bit for bit in bits if bit)
