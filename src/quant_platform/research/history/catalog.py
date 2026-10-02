"""Long-history research series (S3-W01)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Official daily sources and the first date each one can be queried.
TWSE_STOCK_DAY_START = date(2010, 1, 4)   # rwd/zh/afterTrading/STOCK_DAY
TWSE_ETF_DAILY_START = date(2004, 2, 11)  # rwd/zh/afterTrading/MI_INDEX?type=0099P
TAIEX_START = date(2003, 1, 2)            # MI_5MINS_HIST / MFI94U


@dataclass(frozen=True, slots=True)
class Backfill:
    """Synthetic closes before a series listed, chained backward from its first official close:
    daily return = ``leverage`` × the source's total return − ``annual_drag`` / 252. Rows carry
    source "synthetic"; the manifest reports how many and how the rule tracks the real series
    where they overlap. Used so a leveraged ETF can be researched over 2004–2016 at all."""

    source: str
    leverage: float
    annual_drag: float  # expense ratio plus futures financing, fixed a priori (no look-ahead)

    @property
    def rule(self) -> str:
        return f"{self.leverage:g} × {self.source} 含息日報酬 − {self.annual_drag:.1%}／年"


@dataclass(frozen=True, slots=True)
class HistorySeries:
    key: str
    name: str
    kind: str  # twse_etf | tpex_etf | taiex | taiex_tr
    first_month: date  # first month to query (listing month or index start)
    yahoo: str | None = None
    note: str = ""
    tax_kind: str = "stock_etf"  # securities transaction tax class (research/costs.py)
    backfill: Backfill | None = None
    # Splits the official STOCK_DAY rows do not mark: (first day after the split, units after / before).
    declared_splits: tuple[tuple[date, float], ...] = ()

    @property
    def tradable(self) -> bool:
        return self.kind in {"twse_etf", "tpex_etf"}


SERIES: tuple[HistorySeries, ...] = (
    HistorySeries(
        "0050", "元大台灣50", "twse_etf", date(2003, 6, 1), "0050.TW",
        "上市 2003-06-30；官方日線從 2004-02-11 起；2025-06 一拆四",
    ),
    HistorySeries("006208", "富邦台50", "twse_etf", date(2012, 7, 1), "006208.TW"),
    HistorySeries("0056", "元大高股息", "twse_etf", date(2007, 12, 1), "0056.TW"),
    # Sector and style ETFs listed 2006–2008 (rotation research, 2026-10-02): the 2004–2009 ETF
    # daily report in the cache already carries them.
    HistorySeries("0051", "元大中型100", "twse_etf", date(2006, 8, 1), "0051.TW", "上市 2006-08-31"),
    HistorySeries("0052", "富邦科技", "twse_etf", date(2006, 9, 1), "0052.TW", "上市 2006-09-12"),
    HistorySeries("0053", "元大電子", "twse_etf", date(2007, 7, 1), "0053.TW", "上市 2007-07-16"),
    HistorySeries("0055", "元大MSCI金融", "twse_etf", date(2007, 7, 1), "0055.TW", "上市 2007-07-16"),
    HistorySeries("0057", "富邦摩台", "twse_etf", date(2008, 2, 1), "0057.TW", "上市 2008-02-27"),
    HistorySeries(
        "00631L", "元大台灣50正2", "twse_etf", date(2014, 10, 1), "00631L.TW",
        "上市 2014-10-31；單日兩倍的台灣50指數，無配息；上市前以 0050 含息報酬合成，成本 3.5%／年取自 2014–2026 "
        "實際追蹤差（manifest calibration：隱含 3.7%、追蹤誤差 8.8%／年）；2026-03-31 一拆二十二（停牌 4 日後恢復；"
        "證交所日資料無註記，比率依 Yahoo 還原因子 443.15 ÷ 20.1432 = 22.00）",
        backfill=Backfill("0050", 2.0, 0.035), declared_splits=((date(2026, 3, 31), 22.0),),
    ),
    HistorySeries("00646", "元大S&P500", "twse_etf", date(2015, 12, 1), "00646.TW", "上市 2015-12-14"),
    HistorySeries("00662", "富邦NASDAQ", "twse_etf", date(2016, 6, 1), "00662.TW", "上市 2016-06-17"),
    HistorySeries("00713", "元大台灣高息低波", "twse_etf", date(2017, 9, 1), "00713.TW"),
    HistorySeries("00878", "國泰永續高股息", "twse_etf", date(2020, 7, 1), "00878.TW"),
    HistorySeries("00919", "群益台灣精選高息", "twse_etf", date(2022, 10, 1), "00919.TW"),
    HistorySeries(
        "00679B", "元大美債20年", "tpex_etf", date(2017, 1, 1), "00679B.TWO",
        "債券 ETF 證交稅停徵至 2026-12-31（之後暫以免稅計算）", "bond_etf",
    ),
    HistorySeries(
        "00687B", "國泰20年美債", "tpex_etf", date(2017, 4, 1), "00687B.TWO",
        "債券 ETF 證交稅停徵至 2026-12-31（之後暫以免稅計算）", "bond_etf",
    ),
    HistorySeries("TAIEX", "發行量加權股價指數", "taiex", date(2003, 1, 1), "^TWII"),
    HistorySeries("TAIEX_TR", "發行量加權股價報酬指數", "taiex_tr", date(2003, 1, 1)),
)
SERIES_BY_KEY = {item.key: item for item in SERIES}

# Drawdowns the research period must cover (REQUIREMENTS §6, roadmap S3-W01).
STRESS_PERIODS = {
    "2008 金融海嘯": (date(2008, 5, 1), date(2008, 12, 31)),
    "2011 歐債危機": (date(2011, 7, 1), date(2011, 12, 31)),
    "2015 股災": (date(2015, 4, 1), date(2015, 9, 30)),
    "2020 疫情": (date(2020, 1, 15), date(2020, 4, 30)),
    "2022 升息": (date(2022, 1, 1), date(2022, 10, 31)),
}
