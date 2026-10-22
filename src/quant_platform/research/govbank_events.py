"""2026-10-23 (research_method §7「八大行庫」的下一版, written before the run): the government banks' unusually large
buying of one stock as an event — the day its net buying (money) is in the top EVENT_SHARE of its own last LOOKBACK
sessions and above zero, known from the next session (the table comes out in the evening).

Measured from the close of the session after the event (the first session it is known) over the next 5 and 20
sessions, against the average eligible listed stock over the same sessions; events on a day the index fell
MARKET_FALL or more (the banks supporting the market) apart from the others. Periods: 2022-07～2023-12 and
2024-01～2026-08 (the table starts 2021-06-30; LOOKBACK sessions first).

Useful (written first): the 20-session excess at least EDGE above zero in both periods with t of at least 2 (a signal
to buy on), or at least EDGE below in both (a veto); either for all events or for one of the two kinds of day."""

from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
LOOKBACK = 250
EVENT_SHARE = 0.01
MARKET_FALL = -0.015
HORIZONS = (5, 20)
EDGE = 0.01
PERIODS = {"2022-07～2023-12": (date(2022, 7, 1), date(2023, 12, 31)), "2024-01～2026-08": (date(2024, 1, 1), date(2026, 8, 31))}


def events(net: np.ndarray) -> np.ndarray:
    """symbols × sessions: True where the day's net buying is above zero and in its own top EVENT_SHARE of the last
    LOOKBACK sessions (the day itself not counted in the baseline); NaN days are no events."""
    out = np.zeros(net.shape, dtype=bool)
    for column in range(LOOKBACK, net.shape[1]):
        window = net[:, column - LOOKBACK:column]
        with np.errstate(invalid="ignore"):
            cut = np.nanquantile(window, 1 - EVENT_SHARE, axis=1)
        today = net[:, column]
        out[:, column] = np.isfinite(today) & np.isfinite(cut) & (today > 0) & (today > cut)
    return out


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if len(values) < 2:
        return {"events": len(values), "mean": None, "t": None}
    mean, deviation = statistics.fmean(values), statistics.stdev(values)
    return {"events": len(values), "mean": round(mean, 5),
            "t": round(mean / (deviation / math.sqrt(len(values))), 2) if deviation else None}


def run(history: str | Path, research: str | Path) -> Path:
    import pandas as pd

    from quant_platform.research import daily

    history = Path(history)
    _data, fp = daily.load(history)
    table = pd.read_parquet(history / "paid" / "TaiwanStockGovernmentBankBuySell.parquet",
                            columns=["date", "code", "buy_amount", "sell_amount"])
    table["net"] = table["buy_amount"] - table["sell_amount"]
    frame = table.pivot_table(index="date", columns="code", values="net", aggfunc="sum")
    frame.index = pd.to_datetime(frame.index)
    sessions = pd.to_datetime(pd.Index(fp.sessions))
    first = frame.index.min()
    frame = frame.reindex(sessions).fillna(0.0)
    frame.loc[frame.index < first] = np.nan
    codes = [symbol.split(".")[0] for symbol in fp.symbols]
    net = frame.reindex(columns=codes).to_numpy(dtype=float).T          # symbols × sessions (NaN: no data yet)
    net[:, (sessions < first)] = np.nan
    found = events(net)
    close = fp.panel.filled
    base = daily.DailyRule(name="資格", factors={"trend_200": 1.0})
    taiex = np.asarray(fp.market, dtype=float) if getattr(fp, "market", None) is not None else None   # TAIEX closes
    rows = {"all": {period: {h: [] for h in HORIZONS} for period in PERIODS},
            "market_fell": {period: {h: [] for h in HORIZONS} for period in PERIODS},
            "normal": {period: {h: [] for h in HORIZONS} for period in PERIODS}}
    for column in range(LOOKBACK, len(fp.sessions) - max(HORIZONS) - 2):
        day = fp.sessions[column]
        period = next((name for name, (start, end) in PERIODS.items() if start <= day <= end), None)
        if period is None or not found[:, column].any():
            continue
        entry = column + 1                                   # known the next session, bought at its close
        eligible = fp.eligible(base, entry)
        market = None
        if taiex is not None and column > 0 and np.isfinite(taiex[column]) and np.isfinite(taiex[column - 1]):
            market = taiex[column] / taiex[column - 1] - 1
        kind = "market_fell" if market is not None and market <= MARKET_FALL else "normal"
        for horizon in HORIZONS:
            with np.errstate(invalid="ignore", divide="ignore"):
                ahead = close[:, entry + horizon] / close[:, entry] - 1
            average = np.nanmean(ahead[eligible & np.isfinite(ahead)]) if (eligible & np.isfinite(ahead)).any() else np.nan
            for row in np.flatnonzero(found[:, column] & eligible & np.isfinite(ahead)):
                excess = float(ahead[row] - average)
                rows["all"][period][horizon].append(excess)
                rows[kind][period][horizon].append(excess)
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "lookback": LOOKBACK,
              "event_share": EVENT_SHARE, "market_fall": MARKET_FALL, "edge": EDGE, "results": {}}
    for kind, periods in rows.items():
        report["results"][kind] = {period: {str(h): summarize(values) for h, values in horizons.items()}
                                   for period, horizons in periods.items()}
    verdicts = {}
    for kind, periods in report["results"].items():
        twenty = [periods[period]["20"] for period in PERIODS]
        buy = all(item["mean"] is not None and item["mean"] >= EDGE and (item["t"] or 0) >= 2 for item in twenty)
        veto = all(item["mean"] is not None and item["mean"] <= -EDGE and (item["t"] or 0) <= -2 for item in twenty)
        verdicts[kind] = "買進訊號" if buy else ("否決" if veto else "沒用")
    report["verdicts"] = verdicts
    folder = Path(research) / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"govbank-events-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
