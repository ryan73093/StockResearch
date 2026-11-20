"""2026-11-19 (使用者：做一個選股的 AI agent，讓這個 agent 去幫你選擇要買還是賣；它有的資訊更多、基準更多，不是以前那種大的深度
模型，是用 LLM 的 agent): the stock-picking agent, a forward-only experiment in a simulated account (D-001, D-007: no
real order, ever).

The LLM trader of 10/20 (research/llm_trader.py) answers once a day and may only pick among 60 candidates it is handed.
This agent works in steps: each step it either asks for a tool or gives its decision, up to STEPS a day.

- tools (TOOLS; every one reads only what was known at the day's close): the market's state, a screen of the whole
  market by any signal, the picks the month's passing rules agree on, a stock's card (returns, 24 factor percentiles
  of every family, its beta to the market, the fortnight's scored news), its own portfolio with each holding's result
  since bought, and its journal — its last views and the lessons it wrote, with how the stocks it sold went;
- the decision: at most HOLD stocks, equal parts, listed common stocks trading that day (no ETF, at most a third from
  one industry), a reason each, a view of the market and a lesson for its journal. A stock it names must exist and be
  tradable; an answer that breaks a rule, or none, keeps the previous day's choice (recorded as such);
- the mechanical twin (control) holds the stocks the passing rules agree on most, keeping what it holds while it is
  still among them — the same information the agent starts from, without the agent.

Both lists are stored (forward/llm_agent/decisions.jsonl, one line a day with the whole trace of tool calls, never
rewritten) and become the scores of two rule factors, ``llm_agent`` and ``llm_agent_control`` (``scores``): a day's
choice ranks its stocks on the next session, so the forward accounts trade them at the next close. A language model
read the years a backtest would cover, so only the forward record judges it (as the LLM trader); the days before it
existed are decided afterwards with that day's information and marked ``replayed`` — not evidence from before.

2026-11-20 (research/agent_lab): rule 2 of the instructions said the money not placed stays in cash; the engine puts
the whole account in the stocks chosen, each an equal part (``simulate_daily``: 1 / len(picks)). The sentence now says
what happens (the prompt version changes with it; the 60 sessions judged start with this day's decision).

2026-11-20 (使用者：看研究中交易的策略、勝率較高的再參考；設規則要有依據; version 1.1.0, before its first Haiku decision):
the account holds 20 — the agent is asked for 20 and ``complete`` fills a shorter list from the agreed stocks — and no
stock under the exchange's disposition is bought anew. The evidence is in docs/research/results.md「代理人的硬規則依據」:
of the forward rules on the longest chained record, every one that held up against 0050 by both moneys holds 20 and
none of the 104 holding 10 did; the platform's factor study found the attention and disposition lists fit as
exclusions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

VERSION = "llm-agent-1.1.0"           # 2026-11-20: 20 held (filled from the agreed), no new buys under disposition
OPERATION = "llm_agent"
HOLD = 20
STEPS = 8                     # tool rounds a day; the last step must be the decision
INDUSTRY_SHARE = 0.3
NEWS_DAYS = 14
PANEL_FAMILIES = 10           # the month's passing rules the "consensus" tool reads: the best of each family
CONSENSUS_TOP = 30
SCREEN_MOST = 30
CARD_MOST = 8
TAIPEI = ZoneInfo("Asia/Taipei")
# the factors of a stock's card, by family (percentile among the day's stocks; 100 = the factor's best)
CARD_FACTORS = {
    "趨勢動能": ("momentum_12_1", "momentum_6", "momentum_3", "trend_200", "high_52w", "breakout_55"),
    "風險": ("low_volatility_60", "low_ivol_60", "low_max_return", "low_beta_250", "low_down_beta_250"),
    "籌碼": ("foreign_buy_20", "trust_buy_20", "dealer_buy_20", "margin_growth_20", "low_sbl_short", "holders_down_4w",
           "retail_down_4w"),
    "基本面": ("eps_growth", "roe_ttm", "gross_margin", "operating_margin_change", "earnings_yield", "book_to_price",
            "dividend_yield"),
    "模型": ("ml_deep2_top", "ml_gbm_j", "ml_gbm_i"),
}
INSTRUCTIONS = """你是台股研究平台的「AI 選股代理人」前向實驗（模擬帳戶，永遠不會真的下單）。每天收盤後你決定明天收盤要持有哪些上市個股
（最多 20 檔、等額）。帳戶的比較基準是同樣的錢全部買 0050；目標是賺到的錢不輸 0050，而且 0050 下跌時不要跌得比它多。

你每一步只能回覆一個 JSON 物件，二選一：
A. 要查資料：{"tool": "工具名稱", "args": {…}, "why": "為什麼查（20 字內）"}
B. 做出決定：{"hold": [{"code": "2330", "reason": "30 字內的理由"}], "view": "今天的市場看法（60 字內）",
              "lesson": "給之後的自己的一句話：這次學到或要注意什麼（60 字內）"}

工具：
- market：大盤現在的狀態（加權指數與 200 日均線、近期漲跌、多少股票站上年線、最強與最弱的產業）。args: {}
- consensus：這個月過門檻的量化規則今天共同挑的股票（幾個規則把它排進前 20 名）。args: {"top": 30}
- screen：用一個訊號掃全市場，看排最前面的股票。args: {"signal": "訊號名稱", "top": 20}；訊號名稱見下面清單
- card：看幾檔股票的完整資料（漲跌、各類因子的百分位、對大盤的連動、近兩週新聞）。args: {"codes": ["2330", "2317"]}（一次最多 8 檔）
- portfolio：你現在的持股，每一檔買進以來的報酬和同期大盤。args: {}
- journal：你最近幾天的看法、寫給自己的話，以及你賣掉的股票後來怎麼樣。args: {}

規則：
1. 只能持有上市普通股（四位數代號、不是 00 開頭的 ETF），而且必須是你今天查到過或已經持有的股票。
2. 持有 20 檔：請選 20 檔，同一產業最多 6 檔；不夠 20 檔時，程式照規則共識的順序補滿。帳戶的錢平均放在這 20 檔、不會留現金。處置中的股票不能新買（已經持有的可以續抱）。
3. 你的價值在於把各種資訊合起來判斷：量化規則的共識、因子、連動（連動高的股票大盤跌時跌更多）、新聞、你自己過去的對錯。
   不要只照某一個名次排，也不要每天大幅換股（每次買賣都有費用）。
4. 最多查 7 次，第 8 步一定要做出決定。資料夠了就早點決定。
5. 理由、看法、給自己的話都用繁體中文。"""
PROMPT_VERSION = hashlib.sha256(INSTRUCTIONS.encode("utf-8")).hexdigest()[:12]


def build_client(settings, research: str | Path):
    """The model client inside the agent's share of the monthly LLM budget; None without a key."""
    from quant_platform.research.agent.llm import UsageLedger, model_client

    # 2026-11-20 (D-051): the provider follows the configured model; None without its key
    return model_client(settings, UsageLedger(Path(research) / "agent" / "usage.jsonl"),
                        settings.llm_agent_monthly_budget_usd, operations=(OPERATION,),
                        total_budget_usd=settings.llm_monthly_budget_usd, label="AI 選股代理人")


def folder(research: str | Path) -> Path:
    return Path(research) / "forward" / "llm_agent"


def decisions(research: str | Path) -> list[dict[str, object]]:
    path = folder(research) / "decisions.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def screen_signals() -> list[str]:
    """The signals the screen tool takes: every search signal (with the two beta factors)."""
    from quant_platform.research.scan import signals

    return signals()


# --- what the agent may read: everything as of the day's close ---------------------------------------------------
class Desk:
    """One day's view of the loaded factors for the tools (``position``: the day's session)."""

    def __init__(self, fp, base: Path, research: Path, day: date, held: list[str], history: list[dict]) -> None:
        from quant_platform.research.daily import DailyRule
        from quant_platform.research.stock_forward import _names

        self.fp, self.base, self.research, self.day = fp, Path(base), Path(research), day
        self.position = fp.index[day]
        self.held, self.history = list(held), history
        self.names = _names(self.base, day.year)
        self.close = np.asarray(fp.panel.filled, dtype=float)
        self.tradable = np.asarray(fp.eligible(DailyRule(name="資格", factors={"trend_200": 1.0}), self.position), dtype=bool)
        self.seen: set[str] = set(held)
        self._panel = None
        self._news = None

    # helpers
    def code_of(self, symbol: str) -> str:
        return symbol.split(".")[0]

    def label(self, code: str) -> str:
        return f"{code} {self.names.get(code, '')}".strip()

    def allowed(self, code: str) -> bool:
        row = self.fp.row.get(f"{code}.TW")
        return (len(code) == 4 and code.isdigit() and not code.startswith("00") and row is not None
                and bool(self.tradable[row]))

    def percentile(self, factor: str, row: int) -> int | None:
        try:
            column = self.fp.matrix(factor)[:, self.position]
        except Exception:  # noqa: BLE001 - a factor this panel cannot compute is left out of the card
            return None
        known = np.isfinite(column)
        value = column[row]
        if not np.isfinite(value) or not known.any():
            return None
        return int(round(100 * float((column[known] < value).mean())))

    def change(self, row: int, sessions: int, at: int | None = None) -> float | None:
        at = self.position if at is None else at
        if at - sessions < 0:
            return None
        now, before = self.close[row, at], self.close[row, at - sessions]
        return float(now / before - 1) if np.isfinite(now) and np.isfinite(before) and before > 0 else None

    def market_change(self, start: int, end: int | None = None) -> float | None:
        market = self.fp.market
        end = self.position if end is None else end
        if market is None or start < 0:
            return None
        return float(market[end] / market[start] - 1) if market[start] else None

    def news(self) -> dict[str, list[str]]:
        if self._news is None:
            import pandas as pd

            output: dict[str, list[str]] = {}
            events = self.base / "news_events"
            known = [path for path in sorted(events.glob("*.parquet")) if path.stem <= self.day.strftime("%Y%m%d")] \
                if events.is_dir() else []
            for path in known[-NEWS_DAYS:]:
                frame = pd.read_parquet(path)
                for row in frame[frame["about"].fillna(False)].to_dict("records"):
                    mark = {1: "利多", -1: "利空", 0: "中性"}.get(int(row.get("direction") or 0), "")
                    output.setdefault(str(row["stock_id"]), []).append(
                        f"{row['news_day']} {mark}{'・風險' if row.get('risk') else ''}：{str(row['title'])[:50]}")
            self._news = {code: lines[-4:] for code, lines in output.items()}
        return self._news

    def panel(self) -> list[tuple[str, object]]:
        """(family, rule): the best single rule of each family on the month's list (by money since 2020-10)."""
        if self._panel is None:
            from quant_platform.research.blend import parse_spec
            from quant_platform.research.categories import classify
            from quant_platform.research.rolling import cutoff_for

            path = self.research / "rolling" / f"{cutoff_for(self.day).isoformat()}.json"
            if not path.is_file():      # 2026-11-19: 9/30's month (cutoff 7/31) has no list — the latest list cut off by the day
                earlier = sorted(item for item in (self.research / "rolling").glob("20??-??-??.json")
                                 if item.stem <= self.day.isoformat())
                path = earlier[-1] if earlier else path
            best: dict[str, tuple[float, dict]] = {}
            if path.is_file():
                for row in json.loads(path.read_text(encoding="utf-8")).get("graded") or []:
                    spec = row.get("spec") or {}
                    factors = spec.get("factors") or {}
                    if not row.get("passed") or spec.get("sleeves") or not factors or any(name.startswith("llm_") for name in factors):
                        continue
                    family = str(classify(spec).get("family"))
                    money = float((row.get("metrics") or {}).get("since_2020_final_value") or 0.0)
                    if family not in best or money > best[family][0]:
                        best[family] = (money, spec)
            chosen = sorted(best.items(), key=lambda item: -item[1][0])[:PANEL_FAMILIES]
            self._panel = []
            for family, (_money, spec) in chosen:
                try:
                    self._panel.append((family, parse_spec(spec)))
                except Exception:  # noqa: BLE001 - a rule this code no longer reads is left out
                    continue
        return self._panel

    def agreed(self, top: int = CONSENSUS_TOP) -> list[tuple[str, int, list[str]]]:
        """(code, how many panel rules rank it in their top 20, which families), most agreed first."""
        votes: dict[str, list[str]] = {}
        best: dict[str, int] = {}
        for family, rule in self.panel():
            try:
                picks = self.fp.ranked(rule, self.position)[:20]
            except Exception:  # noqa: BLE001
                continue
            for rank, symbol in enumerate(picks, start=1):
                code = self.code_of(symbol)
                votes.setdefault(code, []).append(family)
                best[code] = min(best.get(code, 99), rank)
        rows = sorted(votes.items(), key=lambda item: (-len(item[1]), best[item[0]], item[0]))
        return [(code, len(families), families) for code, families in rows if self.allowed(code)][:top]

    # tools: each returns text
    def tool_market(self, _args: dict) -> str:
        fp, at = self.fp, self.position
        lines = [f"日期 {self.day.isoformat()} 收盤後。"]
        market = fp.market
        if market is not None:
            average = float(np.nanmean(market[max(0, at - 199): at + 1]))
            high = float(np.nanmax(market[max(0, at - 249): at + 1]))
            lines.append(f"加權指數 {market[at]:.0f}，200 日均線 {average:.0f}（{'站上' if market[at] >= average else '跌破'}），"
                         f"離一年高點 {100 * (market[at] / high - 1):+.1f}%；"
                         + "、".join(f"近 {span} 日 {100 * (self.market_change(at - span) or 0):+.1f}%" for span in (5, 20, 60) if at - span >= 0))
        try:
            trend = fp.matrix("trend_200")[:, at]
            known = np.isfinite(trend) & self.tradable
            lines.append(f"可交易的 {int(known.sum())} 檔裡 {100 * float((trend[known] > 0).mean()):.0f}% 站上自己的 200 日均線。")
        except Exception:  # noqa: BLE001
            pass
        groups: dict[str, list[float]] = {}
        for symbol, row in fp.row.items():
            if self.tradable[row]:
                moved = self.change(row, 20)
                if moved is not None:
                    groups.setdefault(fp.industry(symbol), []).append(moved)
        ranked = sorted(((float(np.median(values)), name) for name, values in groups.items() if len(values) >= 5), reverse=True)
        if ranked:
            lines.append("近 20 日最強的產業（中位數）：" + "、".join(f"{name} {100 * value:+.1f}%" for value, name in ranked[:5]))
            lines.append("最弱的產業：" + "、".join(f"{name} {100 * value:+.1f}%" for value, name in ranked[-5:]))
        return "\n".join(lines)

    def tool_consensus(self, args: dict) -> str:
        top = max(5, min(int(args.get("top") or CONSENSUS_TOP), 40))
        rows = self.agreed(top)
        self.seen.update(code for code, _count, _families in rows)
        families = "、".join(family for family, _rule in self.panel())
        return (f"這個月過門檻的規則裡每一類最好的 {len(self.panel())} 個（{families}）今天各自的前 20 名，被最多規則挑到的：\n"
                + "\n".join(f"{self.label(code)}｜{self.fp.industry(code + '.TW')}｜{count} 個規則（{'、'.join(names)}）"
                            f"{'｜持有中' if code in self.held else ''}" for code, count, names in rows))

    def tool_screen(self, args: dict) -> str:
        signal = str(args.get("signal") or "")
        if signal not in screen_signals():
            return f"沒有這個訊號：{signal}。可用的訊號：" + "、".join(screen_signals())
        top = max(5, min(int(args.get("top") or 20), SCREEN_MOST))
        column = self.fp.matrix(signal)[:, self.position].astype(float)
        column = np.where(self.tradable, column, np.nan)
        order = [int(row) for row in np.argsort(-np.nan_to_num(column, nan=-np.inf)) if np.isfinite(column[row])][:top]
        lines = []
        for place, row in enumerate(order, start=1):
            symbol = self.fp.symbols[row]
            code = self.code_of(symbol)
            if not self.allowed(code):
                continue
            self.seen.add(code)
            moved = self.change(row, 20)
            lines.append(f"{place}. {self.label(code)}｜{self.fp.industry(symbol)}｜近 20 日 {100 * moved:+.1f}%" if moved is not None
                         else f"{place}. {self.label(code)}｜{self.fp.industry(symbol)}")
        return f"訊號 {signal} 今天排最前面的 {len(lines)} 檔（可交易的上市股）：\n" + "\n".join(lines)

    def tool_card(self, args: dict) -> str:
        codes = [str(code).strip() for code in (args.get("codes") or [])][:CARD_MOST]
        blocks = []
        news = self.news()
        for code in codes:
            row = self.fp.row.get(f"{code}.TW")
            if row is None or not self.allowed(code):
                blocks.append(f"{code}：不是可交易的上市普通股，不能持有。")
                continue
            self.seen.add(code)
            symbol = f"{code}.TW"
            moves = "、".join(f"{span} 日 {100 * value:+.1f}%" for span in (5, 20, 60, 250)
                             if (value := self.change(row, span)) is not None)
            lines = [f"{self.label(code)}｜{self.fp.industry(symbol)}｜{'持有中' if code in self.held else '未持有'}｜漲跌：{moves}"]
            for family, factors in CARD_FACTORS.items():
                values = [f"{factor}={value}" for factor in factors if (value := self.percentile(factor, row)) is not None]
                if values:
                    lines.append(f"  {family}（百分位，100 最好）：{' '.join(values)}")
            try:
                beta = -float(self.fp.matrix("low_beta_250")[row, self.position])
                if np.isfinite(beta):
                    lines.append(f"  對大盤的連動（250 日 beta）：{beta:.2f}")
            except Exception:  # noqa: BLE001
                pass
            lines.append("  新聞：" + (" / ".join(news[code]) if code in news else "近兩週沒有評分過的新聞"))
            blocks.append("\n".join(lines))
        return "\n".join(blocks) if blocks else "沒有給股票代號。"

    def bought_at(self, code: str) -> int | None:
        """The session position of the decision that first held ``code`` in the current unbroken stretch."""
        first = None
        for item in reversed(self.history):
            if code in set((item.get("llm") or {}).get("hold") or []):
                first = item["date"]
            else:
                break
        if first is None:
            return None
        return self.fp.index.get(date.fromisoformat(first))

    def tool_portfolio(self, _args: dict) -> str:
        if not self.held:
            return "目前沒有持股（全部是現金）。"
        lines = []
        for code in self.held:
            row = self.fp.row.get(f"{code}.TW")
            since = self.bought_at(code)
            if row is None or since is None:
                lines.append(f"{self.label(code)}｜持有中")
                continue
            start = min(since + 1, self.position)                    # filled at the next close
            now, before = self.close[row, self.position], self.close[row, start]
            mine = float(now / before - 1) if np.isfinite(now) and np.isfinite(before) and before > 0 else None
            index = self.market_change(start)
            lines.append(f"{self.label(code)}｜{self.fp.industry(code + '.TW')}｜持有 {self.position - start} 個交易日｜"
                         + (f"買進以來 {100 * mine:+.1f}%" if mine is not None else "買進以來 —")
                         + (f"，同期大盤 {100 * index:+.1f}%" if index is not None else ""))
        return f"目前持有 {len(self.held)} 檔：\n" + "\n".join(lines)

    def tool_journal(self, _args: dict) -> str:
        if not self.history:
            return "還沒有之前的決定。"
        lines = []
        for item in self.history[-5:]:
            llm = item.get("llm") or {}
            lines.append(f"{item['date']}：看法「{llm.get('view', '')}」；給自己的話「{llm.get('lesson', '')}」"
                         + (f"（{llm['fallback']}）" if llm.get("fallback") else ""))
        sold = []
        for before, after in zip(self.history[-11:-1], self.history[-10:]):
            gone = set((before.get("llm") or {}).get("hold") or []) - set((after.get("llm") or {}).get("hold") or [])
            at = self.fp.index.get(date.fromisoformat(after["date"]))
            for code in sorted(gone):
                row = self.fp.row.get(f"{code}.TW")
                if row is None or at is None or at + 1 > self.position:
                    continue
                moved = self.change(row, self.position - (at + 1), self.position) if self.position > at + 1 else None
                index = self.market_change(at + 1) if self.position > at + 1 else None
                if moved is not None:
                    sold.append(f"{self.label(code)}（{after['date']} 決定賣）之後 {100 * moved:+.1f}%"
                                + (f"、大盤 {100 * index:+.1f}%" if index is not None else ""))
        if sold:
            lines.append("最近賣掉的股票後來：" + "；".join(sold[-12:]))
        return "\n".join(lines)


TOOLS = ("market", "consensus", "screen", "card", "portfolio", "journal")


def disposition(base: str | Path, day: date) -> set[str]:
    """The stocks under the exchange's disposition on ``day`` as known before it: announced on an earlier day (after
    a close), still running (chips/AlertDisposition.parquet, research/history/alerts.py). 2026-11-20: no agent buys
    them anew (docs/research/results.md「代理人的硬規則依據」)."""
    import pandas as pd

    path = Path(base) / "chips" / "AlertDisposition.parquet"
    if not path.is_file():
        return set()
    try:
        frame = pd.read_parquet(path, columns=["date", "code", "end"])
    except Exception:  # noqa: BLE001 - no table: nothing is blocked
        return set()
    announced, ends = pd.to_datetime(frame["date"], errors="coerce"), pd.to_datetime(frame["end"], errors="coerce")
    stamp = pd.Timestamp(day)
    return set(frame.loc[(announced < stamp) & (ends >= stamp), "code"].astype(str))


def control_choice(agreed: list[str], held: list[str], blocked: set[str] | frozenset = frozenset()) -> list[str]:
    """The twin: holdings still among the agreed stocks, then the most agreed, up to HOLD; ``blocked`` stocks are
    not bought anew (2026-11-20: under disposition), holdings are kept as usual."""
    kept = [code for code in held if code in agreed][:HOLD]
    for code in agreed:
        if len(kept) >= HOLD:
            break
        if code not in kept and code not in blocked:
            kept.append(code)
    return kept


def complete(codes: list[str], agreed: list[str], held: list[str], blocked: set[str], industry,
             most: int = HOLD) -> tuple[list[str], list[str], list[str]]:
    """2026-11-20 (使用者：設規則要有依據; docs/research/results.md「代理人的硬規則依據」): an agent's list as the account
    will hold it — (codes, filled, dropped). A new buy of a ``blocked`` stock is dropped (a holding stays); the list
    is filled to ``most`` from the agreed stocks in their order, never past a third of the places in one industry.
    Evidence: every forward rule that held up holds 20; none of the 104 holding 10 did."""
    held_set = set(held)
    dropped = [code for code in codes if code in blocked and code not in held_set]
    output = [code for code in codes if code not in dropped][:most]
    limit = max(1, int(most * INDUSTRY_SHARE))
    counts: dict[str, int] = {}
    for code in output:
        counts[industry(code)] = counts.get(industry(code), 0) + 1
    filled = []
    for code in agreed:
        if len(output) >= most:
            break
        if code in output or code in blocked or counts.get(industry(code), 0) >= limit:
            continue
        output.append(code)
        filled.append(code)
        counts[industry(code)] = counts.get(industry(code), 0) + 1
    return output, filled, dropped


def validate(answer: dict, allowed, seen: set[str], industry) -> tuple[list[str], list[str], str | None]:
    """(codes, reasons, problem): the decision checked against the hard rules; a problem means it is not used."""
    rows = answer.get("hold") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        return [], [], "回覆格式不對（沒有 hold 清單）"
    codes, reasons = [], []
    for row in rows:
        code = str((row or {}).get("code", "")).strip() if isinstance(row, dict) else ""
        if not code or code in codes:
            continue
        if not allowed(code):
            return [], [], f"{code} 不是可交易的上市普通股"
        if code not in seen:
            return [], [], f"{code} 今天沒有查過、也不是持股"
        codes.append(code)
        reasons.append(str(row.get("reason", ""))[:60])
    if len(codes) > HOLD:
        return [], [], f"挑了 {len(codes)} 檔，超過 {HOLD} 檔"
    counts: dict[str, int] = {}
    for code in codes:
        counts[industry(code)] = counts.get(industry(code), 0) + 1
    limit = max(1, int(HOLD * INDUSTRY_SHARE))
    crowded = [name for name, count in counts.items() if count > limit]
    if crowded:
        return [], [], f"同一產業超過 {limit} 檔（{crowded[0]}）"
    return codes, reasons, None


def converse(desk: Desk, client, context: str, steps: int = STEPS) -> tuple[dict | None, list[dict], float]:
    """The day's steps: (the decision or None, the trace of tool calls, the cost). Each step sends what was said so
    far; the last step is told to decide."""
    from quant_platform.research.agent.llm import call_cost

    opening = ("今天的起點資料：\n" + desk.tool_market({}) + "\n\n" + desk.tool_portfolio({}) + "\n\n" + desk.tool_journal({})
               + "\n\n" + desk.tool_consensus({"top": CONSENSUS_TOP})
               + "\n\nscreen 可用的訊號：" + "、".join(screen_signals()))
    said = [opening]
    trace: list[dict] = []
    cost = 0.0
    for step in range(1, steps + 1):
        last = step == steps
        prompt = "\n\n".join(said) + (f"\n\n這是第 {step} 步（最多 {steps} 步）。"
                                      + ("這一步一定要做出決定（回覆 B 的 JSON）。" if last else "請回覆一個 JSON 物件：查資料（A）或做出決定（B）。"))
        answer, usage = client.json_call(INSTRUCTIONS, prompt, OPERATION, context=f"{context}-{step}")
        cost += float(call_cost(getattr(client, "model", ""), usage) or 0.0)
        if isinstance(answer, dict) and "hold" in answer:
            return answer, trace, cost
        name = str((answer or {}).get("tool") or "")
        args = (answer or {}).get("args") if isinstance((answer or {}).get("args"), dict) else {}
        if last:
            return None, trace, cost
        result = getattr(desk, f"tool_{name}")(args) if name in TOOLS else f"沒有這個工具：{name}。可用：{'、'.join(TOOLS)}"
        trace.append({"step": step, "tool": name, "args": args, "why": str((answer or {}).get("why", ""))[:60],
                      "chars": len(result)})
        said.append(f"【第 {step} 步你查了 {name} {json.dumps(args, ensure_ascii=False)}】\n{result}")
    return None, trace, cost


def decide(fp, base: Path, research: Path, day: date, client=None, now: datetime | None = None,
           replayed: bool = False) -> dict[str, object] | None:
    """One day's decision on loaded factors; appended to decisions.jsonl."""
    if fp.index.get(day) is None:
        return None
    previous = decisions(research)
    held_llm = list((previous[-1].get("llm") or {}).get("hold") or []) if previous else []
    held_control = list((previous[-1].get("control") or {}).get("hold") or []) if previous else []
    desk = Desk(fp, base, research, day, held_llm, previous)
    agreed = [code for code, _count, _families in desk.agreed(CONSENSUS_TOP)]
    blocked = disposition(base, day)
    record = {"date": day.isoformat(), "version": VERSION, "prompt_version": PROMPT_VERSION,
              # 2026-11-20 (D-051): which model decided — gpt-6-luna until 11/19, Claude Haiku 5.5 from 11/20
              "model": getattr(client, "model", None),
              "decided_at": (now or datetime.now(TAIPEI)).isoformat(timespec="seconds"),
              "panel": [family for family, _rule in desk.panel()], "agreed": agreed,
              "control": {"hold": control_choice(agreed, held_control, blocked)}}
    if replayed:
        record["replayed"] = True
    llm: dict[str, object] = {"hold": held_llm, "fallback": "沒有呼叫模型"}
    if client is not None:
        try:
            answer, trace, cost = converse(desk, client, day.isoformat())
            if answer is None:
                llm = {"hold": held_llm, "fallback": f"沿用前一天：{STEPS} 步內沒有做出決定", "trace": trace, "cost_usd": cost}
            else:
                codes, reasons, problem = validate(answer, desk.allowed, desk.seen, lambda code: fp.industry(f"{code}.TW"))
                if problem:
                    llm = {"hold": held_llm, "fallback": f"沿用前一天：{problem}", "raw": answer, "trace": trace,
                           "cost_usd": cost}
                else:
                    named = dict(zip(codes, reasons, strict=True))
                    codes, filled, dropped = complete(codes, agreed, held_llm, blocked,
                                                      lambda code: fp.industry(f"{code}.TW"))
                    llm = {"hold": codes, "reasons": {code: named.get(code, "程式依規則共識的順序補滿") for code in codes},
                           "view": str(answer.get("view", ""))[:120], "lesson": str(answer.get("lesson", ""))[:120],
                           "trace": trace, "cost_usd": cost}
                    if filled or dropped:            # 2026-11-20: what the program added or took out, kept apart
                        llm.update(filled=filled, dropped=dropped)
        except Exception as error:  # noqa: BLE001 - a failed call keeps yesterday's choice, recorded
            llm = {"hold": held_llm, "fallback": f"沿用前一天：{str(error)[:120]}"}
    record["llm"] = llm
    path = folder(research) / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def run_day(base: Path, research: Path, day: date, client=None, now: datetime | None = None) -> dict[str, object] | None:
    """Decide ``day`` once (None when already decided or not a session)."""
    from quant_platform.research import daily

    if any(item["date"] == day.isoformat() for item in decisions(research)):
        return None
    _data, fp = daily.load(base)
    return decide(fp, base, research, day, client, now)


def replay(base: Path, research: Path, first: date, last: date, client=None, job=None) -> list[dict[str, object]]:
    """Every session from ``first`` to ``last`` not decided yet, in order, each with only what was known that day;
    marked ``replayed`` (the model's training may know those weeks: not evidence from before)."""
    from quant_platform.research import daily

    _data, fp = daily.load(base)
    days = [day for day in fp.sessions if first <= day <= last]
    made = []
    for number, day in enumerate(days):
        if any(item["date"] == day.isoformat() for item in decisions(research)):
            continue
        if job:
            job.update(done=number, total=len(days), current=f"補做 {day.isoformat()} 的決定", force=True)
        record = decide(fp, base, research, day, client, replayed=True)
        if record is not None:
            made.append(record)
    return made


def scores(fp, research: str | Path, which: str) -> np.ndarray:
    """sessions × symbols: each decision's stocks ranked on the session after it (``which``: "llm" or "control"),
    carried until the next decision; NaN elsewhere."""
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    made = {item["date"]: list((item.get(which) or {}).get("hold") or []) for item in decisions(research)}
    current: list[str] | None = None
    for position in range(1, len(fp.sessions)):
        day = fp.sessions[position - 1].isoformat()
        if day in made:
            current = made[day]
        if current is None:
            continue
        for place, code in enumerate(current):
            row = fp.row.get(f"{code}.TW")
            if row is not None:
                output[position, row] = len(current) - place
    return output


def first_decision(research: str | Path) -> str | None:
    made = sorted(item["date"] for item in decisions(research))
    return made[0] if made else None


def trade_reasons(research: str | Path, days: list[dict]) -> dict[str, dict[str, str]]:
    """day → code → why, in the agent's own words (as llm_trader.trade_reasons)."""
    made = sorted(decisions(research), key=lambda item: item["date"])
    output: dict[str, dict[str, str]] = {}
    for record in days:
        day = str(record["date"])
        recent = [item for item in made if item["date"] < day][::-1][:3]
        for trade in record.get("trades_today") or []:
            code = str(trade["code"])
            for item in recent:
                llm = item.get("llm") or {}
                tag = "（補做）" if item.get("replayed") else ""
                text = None
                if trade["side"] == "BUY" and code in (llm.get("reasons") or {}):
                    text = f"AI 代理人 {item['date']}{tag}：{llm['reasons'][code]}"
                elif trade["side"] == "SELL" and code not in set(llm.get("hold") or []):
                    text = (f"AI 代理人 {item['date']}{tag}的選擇裡沒有它了"
                            + (f"（{llm['fallback']}）" if llm.get("fallback") else ""))
                if text:
                    output.setdefault(day, {})[code] = text
                    break
    return output
