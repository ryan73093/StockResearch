"""2026-10-22 (使用者：我所有的失敗紀錄都要記起來，作為知識庫，讓後面的人可以用 AI 問他的決策、確認可不可行、回測狀況):
the research knowledge base — every experiment, failed or passed, as an entry built from the records the platform
already keeps, searched and asked through the model.

| kind | source | one entry per |
|---|---|---|
| 研究紀錄 | docs/research_method.md | a heading or bold-headed part (a long one split at its top-level bullets) |
| 最佳化紀錄 | research_method's optimization log table | row: date, family, what, result, verdict |
| 淘汰方向 | research_method §8 table | row: direction, reason |
| 試驗 | instance/research/trials.jsonl | rule and period (the newest run): tier, money, drawdown, 0050, why it failed |
| 大規模搜尋 | instance/research/scans/*/summary.json | round: candidates, passed the screen, finalists, tiers |
| 組合搜尋 | instance/research/blend_scan/finalists-*.json | round (a rerun of the same pool once) |
| 開發紀錄 | DEVELOPMENT_HISTORY.md | delivery (a long one split at its bullets) |
| 使用者決定 | AGENTS.md「已核定方向」 | bullet |
| 需求 | REQUIREMENTS.md | section (split like the research records) |

``build`` writes instance/research/knowledge/entries.jsonl (rebuilt by ``ensure`` when a source changed); ``search``
ranks entries by BM25 over Chinese character pairs and Latin words, a trial number (#1234) matched exactly; ``ask``
gives the model the best entries and asks for an answer from them only, each fact cited, in Traditional Chinese —
without a model (no key, the budget used up) the entries themselves are the answer. A text entry's verdict is read
from its words (自動判讀); a trial's from its tier, an optimization row's from its own column."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
VERSION = "knowledge-1.0.0"
DOCS = {"research_method": Path("docs") / "research_method.md", "history": Path("DEVELOPMENT_HISTORY.md"),
        "agents": Path("AGENTS.md"), "requirements": Path("REQUIREMENTS.md")}
KINDS = ("研究紀錄", "最佳化紀錄", "淘汰方向", "試驗", "大規模搜尋", "組合搜尋", "開發紀錄", "使用者決定", "需求")
CODES = {"研究紀錄": "R", "最佳化紀錄": "O", "淘汰方向": "X", "試驗": "T", "大規模搜尋": "S", "組合搜尋": "B", "開發紀錄": "H",
         "使用者決定": "D", "需求": "Q"}
FAILED = ("失敗", "淘汰")                      # the verdicts the failure list shows
MAX_TEXT = 2400                                # characters a text entry holds; longer parts split at bullets
OPTIMIZATIONS = "最佳化紀錄（網站"
RETIRED = "已淘汰的方向與原因"
DECISIONS = "已核定方向"
FAIL_WORDS = ("沒過", "未通過", "沒有通過", "失敗", "淘汰", "沒用", "沒有用", "不採用", "沒找到", "沒有找到", "全部 T3",
              "全部是 T3", "作廢", "不做成", "輸 0050", "沒有贏")
PASS_WORDS = ("**過**", "照判定通過", "都過", "個過", "過門檻", "成為最好", "上前緣", "前緣新", "新頂端", "通過")
PASS_TIERS = ("T0 候選", "T0 候選（高風險）", "T0", "T0（高風險）")
OPERATION = "knowledge"
MODEL_ENTRIES = 12                             # entries the model reads per question
MODEL_TEXT = 1600                              # characters of each it reads
INSTRUCTIONS = """你是這個台股研究平台的「研究知識庫」助手。後來的人會問：某個策略或想法有沒有試過、可不可行、回測結果怎樣、當初為什麼這樣決定。
規則：
1. 只能根據下面提供的紀錄回答，不可用外部知識補數字或補結論；每個事實後面標出處，例如 [K3]。
2. 先給一句結論：試過而且失敗／試過而且通過／部分通過／事先登錄還沒有結果／知識庫沒有紀錄。
3. 再寫重點：做了什麼、事先寫的判定、結果數字（期末金額、最大回撤、和同樣現金流買 0050 比、2020-10 起）、失敗原因、紀錄裡寫的下一版做法。
4. 紀錄不夠回答時明說「知識庫沒有這方面的紀錄」，並照平台規範建議怎麼試：先把判定寫進研究方法再跑、2015-06 起挑規則、100% 個股（0050 只當比較基準）、啟動 30 萬＋每月 1 萬、和同樣現金流買 0050 比、前向帳戶從當月截止日隔天起算。
5. 不給個人投資建議、不保證報酬、不下買賣指令。全部用繁體中文。
回覆 JSON：{"conclusion": "一句結論", "answer": "重點，可用 \\n 分段", "citations": ["K1", "K2"]}"""


def folder(research: str | Path) -> Path:
    return Path(research) / "knowledge"


# --- reading the records ------------------------------------------------------------------
def _date(text: str) -> str:
    found = re.search(r"(20\d\d)-(\d\d)-(\d\d)", text)
    return "-".join(found.groups()) if found else ""


def judge(title: str, text: str) -> str:
    """A text entry's verdict from its words: 事先登錄 (written before the run, no result yet), 失敗, 通過, 部分, 說明."""
    if "結論" in title or "路線圖" in title:            # a summary or a plan, not one experiment
        return "說明"
    if "跑之前寫" in title and "結果" not in text:
        return "事先登錄"
    if "結果" in text:                                  # the outcome, not the plan's words, decides
        text = text[text.index("結果"):]
    failed = sum(text.count(word) for word in FAIL_WORDS)
    passed = sum(text.count(word) for word in PASS_WORDS) - text.count("沒過門檻") - text.count("沒有過門檻")
    if failed and passed > 0:
        return "部分"
    if failed:
        return "失敗"
    if passed > 0:
        return "通過"
    return "說明"


def _verdict_of(word: str) -> str:
    """An optimization row's own verdict, in the knowledge base's words."""
    word = word.strip()
    if word.startswith("沒過") or word.startswith("不"):
        return "失敗"
    if word.startswith("過"):
        return "通過"
    if "部分" in word:
        return "部分"
    if word.startswith("進行"):
        return "進行中"
    return word or "說明"


def sections(text: str) -> list[dict[str, object]]:
    """A Markdown file as its parts: every heading, and every paragraph led by a bold title (``**…**``), with the
    line it starts on and the titles above it."""
    parts: list[dict[str, object]] = []
    trail: dict[int, str] = {}
    current = {"title": "", "path": [], "line": 1, "lines": []}
    for number, line in enumerate(text.splitlines(), start=1):
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        bold = re.match(r"^\*\*(.+?)\*\*[：:]?\s*(.*)$", line)
        if heading or bold:
            parts.append(current)
            if heading:
                level = len(heading.group(1))
                trail = {key: value for key, value in trail.items() if key < level}
                trail[level] = heading.group(2).strip()
                current = {"title": heading.group(2).strip(), "path": [trail[key] for key in sorted(trail)][:-1],
                           "line": number, "lines": []}
            else:
                current = {"title": bold.group(1).strip(), "path": [trail[key] for key in sorted(trail)],
                           "line": number, "lines": [bold.group(2)] if bold.group(2) else []}
            continue
        current["lines"].append(line)
    parts.append(current)
    return [part for part in parts if part["title"] or "".join(part["lines"]).strip()]


def _split(body: str) -> list[str]:
    """``body`` in pieces of at most MAX_TEXT characters, cut before top-level bullets."""
    if len(body) <= MAX_TEXT:
        return [body]
    pieces, current = [], ""
    for block in re.split(r"\n(?=- )", body):
        while len(block) > MAX_TEXT:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(block[:MAX_TEXT])
            block = block[MAX_TEXT:]
        if current and len(current) + len(block) + 1 > MAX_TEXT:
            pieces.append(current)
            current = block
        else:
            current = f"{current}\n{block}" if current else block
    if current:
        pieces.append(current)
    return pieces


def _entry(kind: str, key: str, title: str, text: str, date: str = "", verdict: str = "說明", source: str = "",
           link: str = "", family: str = "", trials: list[int] | None = None, judged: bool = False) -> dict[str, object]:
    found = trials if trials is not None else sorted({int(item) for item in re.findall(r"#(\d{2,5})\b", f"{title} {text}")})
    code = (f"T-{found[0]}" if kind == "試驗" and found else
            f"{CODES.get(kind, 'K')}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:10]}")
    return {"id": code, "kind": kind, "title": title,
            "date": date, "verdict": verdict, "judged": judged, "family": family, "text": text.strip(),
            "trials": found[:40], "source": source, "link": link}


def _document_entries(root: Path, name: str, kind: str, link: str, skip: tuple[str, ...] = ()) -> list[dict[str, object]]:
    path = root / DOCS[name]
    if not path.is_file():
        return []
    output = []
    seen: Counter = Counter()        # an entry's id from its title (and which one of that title), not its line:
    for part in sections(path.read_text(encoding="utf-8")):   # an edit above it leaves it the same entry
        title = str(part["title"])
        if any(word in title for word in skip):
            continue
        seen[title] += 1
        body = "\n".join(part["lines"]).strip()
        if len(body) < 15:
            continue
        context = " › ".join(str(item) for item in part["path"] if item)
        pieces = _split(body)
        for number, piece in enumerate(pieces, start=1):
            label = title if len(pieces) == 1 else f"{title}（第 {number}／{len(pieces)} 部分）"
            date = _date(title) or _date(piece) or _date(context)
            verdict = judge(title, piece) if kind == "研究紀錄" else ("決定" if kind == "使用者決定" else "說明")
            output.append(_entry(kind, f"{name}:{title}#{seen[title]}:{number}", label,
                                 (f"（{context}）\n" if context else "") + piece, date, verdict,
                                 f"{DOCS[name].as_posix()}:{part['line']}", link, judged=kind == "研究紀錄"))
    return output


def _table_rows(text: str, heading: str) -> list[list[str]]:
    rows, inside = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            if inside:
                break
            inside = heading in line
            continue
        if inside and line.startswith("|") and not line.startswith("|---"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows[1:] if rows else []           # without the header row


def research_entries(root: Path) -> list[dict[str, object]]:
    path = root / DOCS["research_method"]
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    link = "/project/research_method"
    output = _document_entries(root, "research_method", "研究紀錄", link, skip=(OPTIMIZATIONS, RETIRED))
    for number, cells in enumerate(_table_rows(text, OPTIMIZATIONS)):
        if len(cells) != 5:
            continue
        date, family, what, result, verdict = cells
        output.append(_entry("最佳化紀錄", f"opt:{date}:{family}:{what}", f"{family}：{what}",
                             f"日期 {date}｜家族 {family}\n做了什麼：{what}\n結果：{result}\n判定：{verdict}",
                             date, _verdict_of(verdict), f"{DOCS['research_method'].as_posix()}（最佳化紀錄第 {number + 1} 列）",
                             "/research?tab=overview", family))
    for cells in _table_rows(text, RETIRED):
        if len(cells) < 2:
            continue
        output.append(_entry("淘汰方向", f"retired:{cells[0]}", cells[0], f"已淘汰的方向：{cells[0]}\n原因：{cells[1]}", "",
                             "淘汰", f"{DOCS['research_method'].as_posix()}（第 8 節）", link))
    return output


def _money(value) -> str:
    return f"{float(value) / 1e4:,.0f} 萬" if value is not None else "—"


def _share(value) -> str:
    return f"{float(value):.1%}" if value is not None else "—"


def trial_entries(research: Path) -> list[dict[str, object]]:
    """The newest run of every rule and period in the trial registry."""
    from quant_platform.research.registry import TrialRegistry, newest

    path = research / "trials.jsonl"
    if not path.is_file():
        return []
    try:
        from quant_platform.research.daily import tier
    except Exception:                                   # noqa: BLE001 - a broken engine import leaves tiers out
        tier = None
    output = []
    for record in sorted(newest(TrialRegistry(path).records()).values(), key=lambda item: item.trial_id):
        metrics = record.metrics or {}
        engine = str(metrics.get("engine", ""))
        lines = [f"#{record.trial_id}｜{record.spec_name}"]
        if engine.startswith("daily-") and tier is not None and record.period == "recent":
            grade, reason = tier(metrics)
            verdict = "通過" if grade in PASS_TIERS else ("部分" if grade == "T1" else "失敗")
            lines.append(f"等級：{grade}（{reason}）")
            lines.append(f"2015-06 起期末 {_money(metrics.get('final_value'))}，同樣的錢買 0050 {_money(metrics.get('benchmark_final_value'))}；"
                         f"投入 {_money(metrics.get('contributed'))}；最大回撤 {_share(metrics.get('max_drawdown'))}"
                         f"（0050 {_share(metrics.get('benchmark_max_drawdown'))}）")
            lines.append(f"2020-10 起期末 {_money(metrics.get('since_2020_final_value'))}"
                         f"（0050 {_money(metrics.get('since_2020_benchmark_final_value'))}）")
        else:
            grade = "舊設計"
            excess = metrics.get("full_period_excess")
            verdict = "失敗" if excess is not None and float(excess) <= 0 else "說明"
            lines.append(f"舊設計（{record.period}，{metrics.get('start', '')}～{metrics.get('end', '')}）：年化 {_share(metrics.get('xirr'))}"
                         f"、基準 {_share(metrics.get('benchmark_xirr'))}、超額 {_share(excess)}、最大回撤 {_share(metrics.get('max_drawdown'))}")
        windows = metrics.get("windows") or {}
        if windows:
            lines.append("視窗勝率：" + "、".join(f"{key} {_share((item or {}).get('win_ratio'))}"
                                             for key, item in windows.items() if isinstance(item, dict)))
        if metrics.get("orders_per_month") is not None:
            lines.append(f"每月 {float(metrics['orders_per_month']):.0f} 筆委託、費稅佔投入 {_share(metrics.get('cost_share'))}")
        if metrics.get("reasons"):
            lines.append("沒過的地方：" + "；".join(str(item) for item in metrics["reasons"]))
        if "0050" in record.spec_name:              # 2026-10-09: strategies are 100% stocks
            lines.append("含 0050：使用者 2026-10-09 起策略一律 100% 個股，含 0050 的只留紀錄、不採用")
        lines.append(f"引擎 {engine or '—'}｜期間 {record.period}｜登錄 {str(record.created_at)[:16].replace('T', ' ')}")
        output.append(_entry("試驗", f"trial:{record.trial_id}", f"#{record.trial_id} {record.spec_name}", "\n".join(lines),
                             str(record.created_at)[:10], verdict, "instance/research/trials.jsonl",
                             f"/research/rules/{record.spec_hash}", grade, [record.trial_id]))
    return output


def search_entries(research: Path) -> list[dict[str, object]]:
    output = []
    for path in sorted((research / "scans").glob("*/summary.json")) if (research / "scans").is_dir() else []:
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        results = summary.get("results") or []
        tiers = summary.get("tiers") or {}
        names = "；".join(f"#{item.get('trial_id') or item.get('trial') or ''} {item.get('name', '')}（{item.get('tier', '')}）"
                         for item in results[:20])
        text = (f"{path.parent.name}：{int(summary.get('candidates') or 0):,} 個設定、過快篩 {int(summary.get('passed_screen') or 0):,} 個、"
                f"最後名單 {summary.get('finalists')} 個（" + ("、".join(f"{key} {value} 個" for key, value in sorted(tiers.items())) or "沒有")
                + f"）；開發期 {'～'.join(summary.get('development') or [])}；訊號 {len(summary.get('signals') or [])} 個"
                + (f"（必含 {summary.get('require')}）" if summary.get("require") else "") + f"；{summary.get('minutes')} 分鐘\n最後名單：{names}")
        passed = any(str(key).startswith("T0") for key in tiers)
        stamp = str(summary.get("stamp", ""))
        date = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if len(stamp) >= 8 else ""
        output.append(_entry("大規模搜尋", f"scan:{path.parent.name}", f"大規模搜尋 {path.parent.name}", text, date,
                             "通過" if passed else "失敗", f"instance/research/scans/{path.parent.name}/summary.json",
                             "/research?tab=overview"))
    rounds = {}
    for path in sorted((research / "blend_scan").glob("finalists-*.json")) if (research / "blend_scan").is_dir() else []:
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rounds.setdefault((summary.get("pool"), summary.get("candidates")), (path, summary))
    for (pool, candidates), (path, summary) in rounds.items():
        stamp = str(summary.get("stamp", ""))
        date = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if len(stamp) >= 8 else ""
        text = (f"組合搜尋 {stamp}：策略池 {pool} 個，兩檔、三檔組合 {int(candidates or 0):,} 個；開發期過門檻 {summary.get('passed')} 個；"
                f"最後名單 {len(summary.get('finalists') or [])} 個進引擎（開發期到 {summary.get('dev_end')}）")
        output.append(_entry("組合搜尋", f"blend:{pool}:{candidates}", f"組合搜尋 {stamp}", text, date, "說明",
                             path.name, "/research?tab=overview"))
    return output


def decision_entries(root: Path) -> list[dict[str, object]]:
    path = root / DOCS["agents"]
    if not path.is_file():
        return []
    output, inside, bullet, line_number = [], False, [], 0

    def close():
        if bullet:
            text = " ".join(bullet).strip()
            output.append(_entry("使用者決定", f"agents:{text[:60]}", text[2:60].split("（")[0].split("：")[0], text, _date(text),
                                 "決定", f"AGENTS.md:{line_number}", "/project/handoff"))

    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.startswith("## "):
            close()
            bullet = []
            inside = DECISIONS in line
            continue
        if not inside:
            continue
        if line.startswith("- "):
            close()
            bullet, line_number = [line], number
        elif bullet and line.strip():
            bullet.append(line.strip())
    close()
    return output


def build(root: str | Path, research: str | Path, now: datetime | None = None) -> dict[str, object]:
    """Every entry from the records into knowledge/entries.jsonl, with what it was built from."""
    root, research = Path(root), Path(research)
    entries = [*research_entries(root), *trial_entries(research), *search_entries(research), *decision_entries(root),
               *_document_entries(root, "history", "開發紀錄", "/project/history"),
               *_document_entries(root, "requirements", "需求", "/project/requirements")]
    seen, unique = set(), []
    for entry in entries:
        if entry["id"] in seen:
            continue
        seen.add(entry["id"])
        unique.append(entry)
    target = folder(research)
    target.mkdir(parents=True, exist_ok=True)
    partial = target / "entries.partial"
    partial.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in unique), encoding="utf-8")
    partial.replace(target / "entries.jsonl")
    counts = Counter(entry["kind"] for entry in unique)
    verdicts = Counter(entry["verdict"] for entry in unique)
    meta = {"version": VERSION, "built_at": (now or datetime.now(TAIPEI)).isoformat(timespec="seconds"),
            "entries": len(unique), "kinds": dict(counts), "verdicts": dict(verdicts), "sources": _stamps(root, research)}
    (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    _CACHE.clear()
    return meta


def _sources(root: Path, research: Path) -> list[Path]:
    paths = [root / path for path in DOCS.values()] + [research / "trials.jsonl"]
    paths += sorted((research / "scans").glob("*/summary.json")) if (research / "scans").is_dir() else []
    paths += sorted((research / "blend_scan").glob("finalists-*.json")) if (research / "blend_scan").is_dir() else []
    return paths


def _stamps(root: Path, research: Path) -> dict[str, int]:
    return {str(path): path.stat().st_mtime_ns for path in _sources(root, research) if path.is_file()}


def meta(research: str | Path) -> dict[str, object]:
    try:
        return json.loads((folder(research) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stale(root: str | Path, research: str | Path) -> bool:
    found = meta(research)
    return not found or found.get("version") != VERSION or found.get("sources") != _stamps(Path(root), Path(research))


def ensure(root: str | Path, research: str | Path) -> dict[str, object]:
    """Rebuilt when a source changed since the last build (or never built); the meta."""
    return build(root, research) if stale(root, research) else meta(research)


# --- searching -----------------------------------------------------------------------------
_CACHE: dict[str, object] = {}


def tokens(text: str) -> list[str]:
    """Chinese character pairs, Latin words and numbers (lowercase), trial numbers as #1234."""
    text = text.lower()
    output = [f"#{number}" for number in re.findall(r"#(\d{2,5})\b", text)]
    output += re.findall(r"[a-z][a-z0-9_.]+|\d+(?:\.\d+)?", text)
    for run in re.findall(r"[一-鿿]+", text):
        output += [run] if len(run) == 1 else [run[index:index + 2] for index in range(len(run) - 1)]
    return output


def entries(research: str | Path) -> list[dict[str, object]]:
    return list(_index(research)["entries"])


def _index(research: str | Path) -> dict[str, object]:
    path = folder(research) / "entries.jsonl"
    key = (str(path), path.stat().st_mtime_ns if path.is_file() else 0)
    if _CACHE.get("key") == key:
        return _CACHE
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []
    postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
    lengths = []
    for number, entry in enumerate(rows):
        counts = Counter(tokens(f"{entry['title']} {entry['title']} {entry['text']}"))
        lengths.append(sum(counts.values()))
        for token, count in counts.items():
            postings[token].append((number, count))
    _CACHE.clear()
    _CACHE.update(key=key, entries=rows, postings=dict(postings), lengths=lengths,
                  average=(sum(lengths) / len(lengths)) if lengths else 1.0)
    return _CACHE


RRF = 60                     # reciprocal rank fusion's constant (the VectorDB project's too)
KEYWORD_RANKS = 200          # the keyword ranking's part in the fusion


def semantic(research: str | Path, question: str, root: str | Path | None = None) -> list[str] | None:
    """2026-10-22 (使用者：向量資料庫可用 BGE-M3、Qdrant): entry ids by BGE-M3 dense and sparse vectors in Qdrant, best
    first; None when the vectors are not there (no packages, model or collection) — the keywords alone then."""
    from quant_platform.research import knowledge_vectors as vectors

    if not question.strip() or not vectors.status(research) or not vectors.available():
        return None
    try:
        model = vectors.encoder(root)
        return vectors.query(research, question, model) if model is not None else None
    except Exception:                                   # noqa: BLE001 - the keywords still answer
        import logging

        logging.getLogger(__name__).exception("Knowledge vectors failed")
        return None


def search(research: str | Path, question: str, limit: int = 20, kinds: tuple[str, ...] | None = None,
           verdicts: tuple[str, ...] | None = None, semantic_ids: list[str] | None = None) -> list[dict[str, object]]:
    """The entries best matching ``question``: BM25 alone, or fused (RRF) with ``semantic_ids`` (BGE-M3 in Qdrant);
    a trial number in the question first."""
    index = _index(research)
    rows, postings, lengths, average = index["entries"], index["postings"], index["lengths"], index["average"]
    total = len(rows)
    scores: Counter = Counter()
    for token in set(tokens(question)):
        found = postings.get(token)
        if not found:
            continue
        idf = math.log(1 + (total - len(found) + 0.5) / (len(found) + 0.5))
        for number, count in found:
            scores[number] += idf * count * 2.5 / (count + 1.5 * (0.25 + 0.75 * lengths[number] / average))
    if semantic_ids is not None:                        # 2026-10-22: the two rankings fused
        place = {entry["id"]: number for number, entry in enumerate(rows)}
        fused: Counter = Counter()
        for rank, (number, _score) in enumerate(scores.most_common(KEYWORD_RANKS)):
            fused[number] += 1.0 / (RRF + rank + 1)
        for rank, entry_id in enumerate(semantic_ids):
            if entry_id in place:
                fused[place[entry_id]] += 1.0 / (RRF + rank + 1)
        scores = Counter({number: value * 100 for number, value in fused.items()})
    asked = {int(number) for number in re.findall(r"#(\d{2,5})\b", question)}
    for number, entry in enumerate(rows):
        if asked and entry["kind"] == "試驗" and set(entry["trials"]) & asked:
            scores[number] += 1000.0
    output = []
    for number, score in scores.most_common():
        entry = rows[number]
        if kinds and entry["kind"] not in kinds:
            continue
        if verdicts and entry["verdict"] not in verdicts:
            continue
        output.append({**entry, "score": round(float(score), 3)})
        if len(output) >= limit:
            break
    return output


def evidence(research: str | Path, question: str, semantic_ids: list[str] | None = None) -> list[dict[str, object]]:
    """What the model reads: the best written records and the best trials, at most MODEL_ENTRIES together (a trial
    named in the question always)."""
    found = search(research, question, limit=60, semantic_ids=semantic_ids)
    asked = [entry for entry in found if entry["kind"] == "試驗" and entry["score"] >= 1000]
    written = [entry for entry in found if entry["kind"] != "試驗"][:8]
    trials = [entry for entry in found if entry["kind"] == "試驗" and entry not in asked][:4]
    return (asked + written + trials)[:MODEL_ENTRIES]


def failures(research: str | Path) -> list[dict[str, object]]:
    """Every failed written record (research, optimization log, retired directions, searches), newest first —
    the failed trials are counted, not listed (thousands)."""
    rows = [entry for entry in entries(research) if entry["verdict"] in FAILED and entry["kind"] != "試驗"]
    return sorted(rows, key=lambda entry: entry.get("date") or "", reverse=True)


# --- asking ----------------------------------------------------------------------------------
def _answers_path(research: str | Path) -> Path:
    return folder(research) / "answers.jsonl"


def cached(research: str | Path, question: str) -> dict[str, object] | None:
    """The answer already given to the same question on the same knowledge base (a reload costs nothing)."""
    path = _answers_path(research)
    if not path.is_file():
        return None
    key = _question_key(research, question)
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("key") == key:
            return item
    return None


def _question_key(research: str | Path, question: str) -> str:
    built = meta(research).get("built_at", "")
    return hashlib.sha256(f"{built}|{question.strip()}".encode("utf-8")).hexdigest()[:20]


def ask(research: str | Path, question: str, client=None, now: datetime | None = None,
        semantic_ids: list[str] | None = None) -> dict[str, object]:
    """The model's answer from the best entries ({"conclusion", "answer", "citations", "evidence", "model"}); without
    a client, or when the call fails, the entries alone with why."""
    question = question.strip()[:500]
    found = evidence(research, question, semantic_ids)
    labelled = [{**entry, "cite": f"K{number}"} for number, entry in enumerate(found, start=1)]
    result: dict[str, object] = {"question": question, "evidence": labelled, "model": None, "conclusion": "",
                                 "answer": "", "citations": [], "note": ""}
    if not question:
        return result
    previous = cached(research, question)
    if previous is not None:
        return {**result, **{key: previous[key] for key in ("conclusion", "answer", "citations", "model", "note") if key in previous}}
    if client is None:
        result["note"] = "沒有設定 AI（或本月 AI 預算用完）：下面是最相關的紀錄。"
        return result
    context = "\n\n".join(f"[{entry['cite']}]（{entry['kind']}｜{entry['verdict']}｜{entry.get('date') or '日期不明'}）{entry['title']}\n"
                          f"{entry['text'][:MODEL_TEXT]}" for entry in labelled)
    try:
        payload, _usage = client.json_call(INSTRUCTIONS, f"問題：{question}\n\n紀錄：\n{context}\n\n請以 JSON 物件回覆。",
                                           OPERATION, context="研究知識庫問答")
    except Exception as exc:                            # noqa: BLE001 - the budget, the network: the entries remain
        result["note"] = f"AI 這次沒有回答（{str(exc)[:120]}）：下面是最相關的紀錄。"
        return result
    result.update(model=getattr(client, "model", "model"), conclusion=str(payload.get("conclusion") or "")[:400],
                  answer=str(payload.get("answer") or "")[:6000],
                  citations=[str(item) for item in payload.get("citations") or []][:30])
    record = {"key": _question_key(research, question), "at": (now or datetime.now(TAIPEI)).isoformat(timespec="seconds"),
              "question": question, "conclusion": result["conclusion"], "answer": result["answer"],
              "citations": result["citations"], "model": result["model"], "evidence": [entry["id"] for entry in labelled]}
    path = _answers_path(research)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return result


def build_client(settings, research: str | Path):
    """The model inside its own share of the monthly LLM budget; None without a key."""
    from quant_platform.research.agent.llm import LLMError, ResponsesClient, UsageLedger, resolve_openai_key

    try:
        key = resolve_openai_key(settings.openai_api_key)
    except LLMError:
        return None
    if not key:
        return None
    return ResponsesClient(key, settings.research_agent_model, UsageLedger(Path(research) / "agent" / "usage.jsonl"),
                           settings.knowledge_monthly_budget_usd, operations=(OPERATION,),
                           total_budget_usd=settings.llm_monthly_budget_usd, label="研究知識庫", max_output_tokens=2000)


def recent_questions(research: str | Path, limit: int = 10) -> list[dict[str, object]]:
    path = _answers_path(research)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:][::-1]


def gist(entry: dict[str, object], length: int = 160) -> str:
    """The part of an entry worth a glance: from its result (結果、原因、等級) when it has one."""
    text = str(entry.get("text") or "")
    if text.startswith("（") and "）\n" in text[:200]:
        text = text.split("）\n", 1)[1]                 # the context line of a research record
    for word in ("結果", "原因：", "等級：", "沒過的地方"):
        at = text.find(word)
        if at > 0:
            text = text[at:]
            break
    text = re.sub(r"\s+", " ", text.replace("**", "")).strip()
    return text if len(text) <= length else text[:length] + "…"


def find(research: str | Path, entry_id: str) -> dict[str, object] | None:
    return next((entry for entry in entries(research) if entry["id"] == entry_id), None)


def listed(research: str | Path, kind: str = "", verdict: str = "", limit: int = 200) -> list[dict[str, object]]:
    """Entries of a kind and verdict, newest first (no question needed)."""
    rows = [entry for entry in entries(research) if (not kind or entry["kind"] == kind)
            and (not verdict or entry["verdict"] == verdict)]
    return sorted(rows, key=lambda entry: (entry.get("date") or "", entry["id"]), reverse=True)[:limit]


def counts(research: str | Path) -> list[dict[str, object]]:
    """Per kind: entries, failed, passed, other."""
    table: dict[str, Counter] = defaultdict(Counter)
    for entry in entries(research):
        table[entry["kind"]][entry["verdict"]] += 1
    rows = []
    for kind in KINDS:
        found = table.get(kind)
        if not found:
            continue
        failed = sum(found[word] for word in FAILED)
        rows.append({"kind": kind, "total": sum(found.values()), "failed": failed, "passed": found["通過"],
                     "partial": found["部分"], "other": sum(found.values()) - failed - found["通過"] - found["部分"]})
    return rows


def scan_total(research: str | Path) -> int:
    """The settings the large searches screened, all rounds (only their finalists are registered one by one)."""
    total = 0
    for path in sorted((Path(research) / "scans").glob("*/summary.json")) if (Path(research) / "scans").is_dir() else []:
        try:
            total += int(json.loads(path.read_text(encoding="utf-8")).get("candidates") or 0)
        except (OSError, ValueError):
            continue
    return total


def ai_status(settings, research: str | Path):
    """(client or None, {"ready", "note"}) for the page."""
    try:
        client = build_client(settings, research)
    except Exception:                                   # noqa: BLE001
        client = None
    if client is None:
        return None, {"ready": False, "note": "沒有設定 OpenAI 金鑰：只能搜尋紀錄。"}
    remaining = float(client.budget_status().get("remaining_usd") or 0.0)
    if remaining <= 0:
        return None, {"ready": False, "note": "本月 AI 預算用完：只能搜尋紀錄。"}
    return client, {"ready": True, "note": f"問 AI 每題約 US$0.002（本月知識庫預算還有 US${remaining:.2f}）。"}


EXAMPLES = ("停損有沒有效？", "月營收的策略可不可行？", "強化學習（RL）試過哪些、結果如何？", "#6490 的回測結果？",
            "八大行庫的資料有沒有用？")


# 2026-10-22: a research run whose result nobody wrote down is a failure lost — the daily check lists them
EXPERIMENT_COMMANDS = frozenset({"daily", "scan", "scan-large", "blend-scan", "robustness", "market-signals", "factors",
                                 "intraday", "open-feasibility", "limits-check", "limits-overrides", "window-weight",
                                 "hypotheses", "deep2-diagnose", "exit2", "meta-label"})
RECORDED_FROM = "2026-10-22"          # the rule's day: earlier runs are in the knowledge base through their trials


def experiment_pipelines(script: str) -> dict[str, list[str]]:
    """research-pipeline.ps1's pipelines that run an experiment: name → the research commands they run."""
    output = {}
    for name, block in re.findall(r'^ {4}"([\w-]+)" \{(.*?)^ {4}\}', script, flags=re.M | re.S):
        commands = re.findall(r'"quant_platform\.research", "([\w-]+)"', block)
        if any(command in EXPERIMENT_COMMANDS for command in commands):
            output[name] = commands
    return output


def unrecorded(root: str | Path, research: str | Path, since: str = RECORDED_FROM) -> list[str]:
    """The experiment pipelines finished since ``since`` that neither the research method nor the development history
    names (as `name`)."""
    root, research = Path(root), Path(research)
    script = root / "scripts" / "research-pipeline.ps1"
    if not script.is_file():
        return []
    pipelines = experiment_pipelines(script.read_text(encoding="utf-8-sig"))
    written = "".join((root / DOCS[name]).read_text(encoding="utf-8") for name in ("research_method", "history")
                      if (root / DOCS[name]).is_file())
    missing = []
    for path in sorted((research / "pipelines").glob("*.json")) if (research / "pipelines").is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = str(record.get("name") or path.stem)
        if (name in pipelines and record.get("status") == "done" and str(record.get("finished_at") or "") >= since
                and f"`{name}`" not in written):
            missing.append(name)
    return missing
