"""Official TWSE / TPEx history endpoints with a polite, resumable cache.

Every response is cached under ``raw/`` as returned. Months and days that are
fully in the past are final and never requested again; the current month is
re-requested on every run. Requests are spaced at least ``min_interval``
seconds apart and a refusal (HTML page, HTTP 403/429) stops the run instead
of retrying, so the exchange is not hammered.
"""

from __future__ import annotations

import calendar
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

TWSE = "https://www.twse.com.tw/rwd/zh"
TPEX = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
TPEX_BULLETIN = "https://www.tpex.org.tw/www/zh-tw/bulletin"
EMPTY_MARKERS = ("沒有符合條件", "查詢日期小於", "查無資料")


def _month_end(month: date) -> date:
    return date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])


class SourceRefused(RuntimeError):
    """The exchange refused or rate-limited us; stop and resume later."""


@dataclass(frozen=True, slots=True)
class DailyRow:
    day: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None = None      # shares
    turnover: int | None = None    # TWD
    trades: int | None = None
    note: str = ""
    source: str = ""


def _get_json(url: str, timeout: int = 30, data: bytes | None = None) -> object:
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 StockResearch/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code in {403, 429}:
            raise SourceRefused(f"HTTP {exc.code}：{url}") from exc
        raise
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        # TWSE answers a blocked client with an HTML page.
        raise SourceRefused(f"非 JSON 回應（可能被暫時封鎖）：{url}") from exc


class OfficialHistoryClient:
    def __init__(
        self,
        raw_dir: str | Path,
        fetch_json: Callable[[str], object] | None = None,
        min_interval: float = 3.0,
        today: Callable[[], date] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
        offline: bool = False,
    ) -> None:
        self._raw = Path(raw_dir)
        self._fetch = fetch_json or _get_json
        self._min_interval = min_interval
        self._today = today or date.today
        self._sleep = sleep
        self._retries = retries
        self._offline = offline
        self._last_request = 0.0
        self.requests = 0
        self.cache_hits = 0

    # --- endpoints -------------------------------------------------------
    def twse_stock_month(self, code: str, month: date) -> object:
        query = urlencode({"date": f"{month:%Y%m}01", "stockNo": code, "response": "json"})
        return self._cached(
            f"twse_stock_day/{code}/{month:%Y%m}", f"{TWSE}/afterTrading/STOCK_DAY?{query}",
            final=self._month_is_final(month), period_end=_month_end(month),
        )

    def twse_etf_day(self, day: date) -> object:
        query = urlencode({"date": f"{day:%Y%m%d}", "type": "0099P", "response": "json"})
        return self._cached(
            f"twse_etf_daily/{day:%Y}/{day:%Y%m%d}", f"{TWSE}/afterTrading/MI_INDEX?{query}",
            final=day < self._today(), period_end=day,
        )

    def taiex_month(self, month: date) -> object:
        query = urlencode({"date": f"{month:%Y%m}01", "response": "json"})
        return self._cached(
            f"taiex/{month:%Y%m}", f"{TWSE}/TAIEX/MI_5MINS_HIST?{query}",
            final=self._month_is_final(month), period_end=_month_end(month),
        )

    def taiex_total_return_month(self, month: date) -> object:
        query = urlencode({"date": f"{month:%Y%m}01", "response": "json"})
        return self._cached(
            f"taiex_tr/{month:%Y%m}", f"{TWSE}/TAIEX/MFI94U?{query}",
            final=self._month_is_final(month), period_end=_month_end(month),
        )

    def twse_odd_lot_day(self, day: date) -> object:
        """TWT53U 盤後零股交易行情單 for one session (the 14:30 call auction)."""
        query = urlencode({"date": f"{day:%Y%m%d}", "response": "json"})
        return self._cached(
            f"twse_odd_lot/{day:%Y}/{day:%Y%m%d}", f"{TWSE}/afterTrading/TWT53U?{query}",
            final=day < self._today(), period_end=day,
        )

    def tpex_ex_rights_year(self, year: int) -> object:
        """TPEx 除權除息計算結果表 (the website's own POST query)."""
        body = urlencode({"startDate": f"{year}/01/01", "endDate": f"{year}/12/31", "response": "json"})
        return self._cached(
            f"tpex_ex_rights/{year}", f"{TPEX_BULLETIN}/exDailyQ",
            final=year < self._today().year, data=body.encode("ascii"),
            period_end=date(year, 12, 31),
        )

    def twse_ex_rights_year(self, year: int) -> object:
        """TWT49U 除權除息計算結果表 (data from 2003-05-05)."""
        query = urlencode({"startDate": f"{year}0101", "endDate": f"{year}1231", "response": "json"})
        return self._cached(
            f"twse_ex_rights/{year}", f"{TWSE}/exRight/TWT49U?{query}",
            final=year < self._today().year, period_end=date(year, 12, 31),
        )

    def tpex_stock_month(self, code: str, month: date) -> object:
        query = f"code={quote(code)}&date={month:%Y}%2F{month:%m}%2F01&response=json"
        return self._cached(
            f"tpex_trading/{code}/{month:%Y%m}", f"{TPEX}?{query}",
            final=self._month_is_final(month), period_end=_month_end(month),
        )

    # --- cache and throttle ----------------------------------------------
    def _month_is_final(self, month: date) -> bool:
        today = self._today()
        return (month.year, month.month) < (today.year, today.month)

    def _cached(
        self, key: str, url: str, final: bool, data: bytes | None = None,
        period_end: date | None = None,
    ) -> object:
        """Final periods come from the cache once stored; the current period is
        re-requested but still cached, so an offline rebuild sees the latest copy.

        A copy written on or before ``period_end`` was taken while the period was
        still open, so it is requested once more after the period has closed."""
        path = self._raw / f"{key}.json"
        if path.is_file() and (final or self._offline):
            stale = (
                final and not self._offline and period_end is not None
                and date.fromtimestamp(path.stat().st_mtime) <= period_end
            )
            if not stale:
                self.cache_hits += 1
                return json.loads(path.read_text(encoding="utf-8"))
        if self._offline:
            return None
        payload = self._request(url, data)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".json.partial")
        partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        partial.replace(path)
        return payload

    def _request(self, url: str, data: bytes | None = None) -> object:
        last_error: Exception | None = None
        for attempt in range(self._retries):
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            self.requests += 1
            try:
                return self._fetch(url) if data is None else self._fetch(url, data=data)
            except SourceRefused:
                raise
            except Exception as exc:  # noqa: BLE001 - network errors are retried with backoff
                last_error = exc
                self._sleep(self._min_interval * (3 ** attempt))
        raise RuntimeError(f"下載失敗（重試 {self._retries} 次）：{url}：{last_error}")


# --- parsers ----------------------------------------------------------------

def is_empty_response(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    stat = str(payload.get("stat", ""))
    return any(marker in stat for marker in EMPTY_MARKERS)


def roc_date(text: object) -> date:
    """'92/07/01' → 2003-07-01; TPEx marks some days (e.g. a listing day) with '*'."""
    parts = str(text).strip().replace("＊", "").replace("*", "").strip().split("/")
    year, month, day = (int(part) for part in parts)
    return date(year + 1911 if year < 1911 else year, month, day)


def number(text: object) -> float | None:
    value = str(text if text is not None else "").replace(",", "").strip()
    if value in {"", "--", "---", "X"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def integer(text: object) -> int | None:
    value = number(text)
    return None if value is None else int(round(value))


def parse_twse_stock_month(payload: object) -> list[DailyRow]:
    """STOCK_DAY: 日期, 成交股數, 成交金額, 開, 高, 低, 收, 漲跌, 筆數, 註記."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    rows = []
    for item in payload.get("data") or []:
        if not isinstance(item, list) or len(item) < 9:
            continue
        rows.append(DailyRow(
            day=roc_date(item[0]), open=number(item[3]), high=number(item[4]),
            low=number(item[5]), close=number(item[6]), volume=integer(item[1]),
            turnover=integer(item[2]), trades=integer(item[8]),
            note=str(item[9]).strip() if len(item) > 9 else "", source="twse_stock_day",
        ))
    return rows


def parse_twse_etf_day(payload: object, codes: set[str]) -> dict[str, DailyRow]:
    """MI_INDEX type=0099P: one row per ETF for that day."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return {}
    day = date(int(str(payload["date"])[:4]), int(str(payload["date"])[4:6]), int(str(payload["date"])[6:8]))
    output: dict[str, DailyRow] = {}
    for table in payload.get("tables") or []:
        if not isinstance(table, dict) or (table.get("fields") or [])[:2] != ["證券代號", "證券名稱"]:
            continue
        for item in table.get("data") or []:
            code = str(item[0]).strip()
            if code not in codes or len(item) < 9:
                continue
            output[code] = DailyRow(
                day=day, open=number(item[5]), high=number(item[6]), low=number(item[7]),
                close=number(item[8]), volume=integer(item[2]), turnover=integer(item[4]),
                trades=integer(item[3]), source="twse_etf_daily",
            )
    return output


def parse_taiex_month(payload: object) -> list[DailyRow]:
    """MI_5MINS_HIST: 日期, 開盤指數, 最高指數, 最低指數, 收盤指數."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    return [
        DailyRow(
            day=roc_date(item[0]), open=number(item[1]), high=number(item[2]),
            low=number(item[3]), close=number(item[4]), source="twse_taiex",
        )
        for item in payload.get("data") or [] if isinstance(item, list) and len(item) >= 5
    ]


def parse_taiex_total_return_month(payload: object) -> list[DailyRow]:
    """MFI94U: 日期, 發行量加權股價報酬指數."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    return [
        DailyRow(
            day=roc_date(item[0]), open=None, high=None, low=None, close=number(item[1]),
            source="twse_taiex_tr",
        )
        for item in payload.get("data") or [] if isinstance(item, list) and len(item) >= 2
    ]


@dataclass(frozen=True, slots=True)
class ExRightsEvent:
    day: date
    code: str
    pre_close: float | None
    reference: float | None
    rights_value: float   # 權值 (stock dividend, price terms)
    cash: float           # 息值 (cash dividend per unit)
    kind: str             # 權 / 息 / 權息
    source: str = "twse_ex_rights"


def roc_long_date(text: object) -> date:
    """'94年05月19日' → 2005-05-19."""
    value = str(text).strip()
    year, rest = value.split("年", 1)
    month, rest = rest.split("月", 1)
    return date(int(year) + 1911, int(month), int(rest.rstrip("日")))


def parse_twse_ex_rights(payload: object, codes: set[str]) -> list[ExRightsEvent]:
    """TWT49U: 資料日期, 代號, 名稱, 前收, 參考價, 權值, 息值, 權值+息值, 權/息, …"""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    return _ex_rights_rows(payload.get("data") or [], codes, "twse_ex_rights")


def parse_tpex_ex_rights(payload: object, codes: set[str]) -> list[ExRightsEvent]:
    """TPEx exDailyQ: same columns as TWT49U, dates as 112/01/30."""
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        return []
    rows = [row for table in payload.get("tables") or [] for row in (table or {}).get("data") or []]
    return _ex_rights_rows(rows, codes, "tpex_ex_rights")


def _ex_rights_rows(rows: list, codes: set[str], source: str) -> list[ExRightsEvent]:
    events = []
    for item in rows:
        if not isinstance(item, list) or len(item) < 9:
            continue
        code = str(item[1]).strip()
        if code not in codes:
            continue
        text = str(item[0])
        events.append(ExRightsEvent(
            day=roc_long_date(text) if "年" in text else roc_date(text), code=code,
            pre_close=number(item[3]), reference=number(item[4]),
            rights_value=number(item[5]) or 0.0, cash=number(item[6]) or 0.0,
            kind=str(item[8]).strip(), source=source,
        ))
    return events


def parse_tpex_stock_month(payload: object) -> list[DailyRow]:
    """tradingStock: 日期, 成交仟股, 成交仟元, 開, 高, 低, 收, 漲跌, 筆數."""
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        return []
    rows = []
    for table in payload.get("tables") or []:
        for item in (table or {}).get("data") or []:
            if not isinstance(item, list) or len(item) < 9:
                continue
            thousand_shares = number(item[1])
            thousand_twd = number(item[2])
            rows.append(DailyRow(
                day=roc_date(item[0]), open=number(item[3]), high=number(item[4]),
                low=number(item[5]), close=number(item[6]),
                volume=None if thousand_shares is None else int(round(thousand_shares * 1000)),
                turnover=None if thousand_twd is None else int(round(thousand_twd * 1000)),
                trades=integer(item[8]), source="tpex_trading",
            ))
    return rows
