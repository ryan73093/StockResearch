from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class TaiwanMarketRank:
    symbol: str
    market_value_twd: Decimal
    source: str


class TaiwanOfficialMarketRankingProvider:
    """Ranks TWSE/TPEx companies with official, keyless, cached endpoints."""

    _twse_companies = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    _twse_daily = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    _tpex_value = "https://www.tpex.org.tw/openapi/v1/tpex_daily_market_value"

    def __init__(self, timeout: int = 30, cache_hours: int = 12) -> None:
        self._timeout = timeout
        self._cache_for = timedelta(hours=cache_hours)
        self._cached_at: datetime | None = None
        self._cached: tuple[TaiwanMarketRank, ...] = ()
        self._lock = threading.Lock()

    @staticmethod
    def _decimal(value: object) -> Decimal:
        try:
            return Decimal(str(value).replace(",", "").strip())
        except (InvalidOperation, ValueError):
            return Decimal("0")

    def _request(self, url: str) -> list[dict[str, object]]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "QuantOS-Research/1.0",
                    },
                )
                with urlopen(request, timeout=self._timeout) as response:
                    return list(json.loads(response.read().decode("utf-8-sig")))
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (2 ** attempt))
        raise RuntimeError(f"官方市場排名資料下載失敗：{last_error}")

    def fetch(self, force: bool = False) -> tuple[TaiwanMarketRank, ...]:
        now = datetime.now(UTC)
        with self._lock:
            if (
                not force
                and self._cached
                and self._cached_at is not None
                and now - self._cached_at < self._cache_for
            ):
                return self._cached

            company_rows = self._request(self._twse_companies)
            daily_rows = self._request(self._twse_daily)
            tpex_rows = self._request(self._tpex_value)
            issued_shares = {
                str(item.get("公司代號") or "").strip():
                self._decimal(item.get("已發行普通股數或TDR原股發行股數"))
                for item in company_rows
            }
            rankings: list[TaiwanMarketRank] = []
            for item in daily_rows:
                code = str(item.get("Code") or "").strip()
                if len(code) != 4 or not code.isdigit():
                    continue
                close = self._decimal(item.get("ClosingPrice"))
                shares = issued_shares.get(code, Decimal("0"))
                if close > 0 and shares > 0:
                    rankings.append(TaiwanMarketRank(
                        symbol=f"{code}.TW",
                        market_value_twd=close * shares,
                        source="twse-openapi-issued-shares-x-close",
                    ))
            for item in tpex_rows:
                code = str(item.get("SecuritiesCompanyCode") or "").strip()
                if len(code) != 4 or not code.isdigit():
                    continue
                # TPEx documents the ranking value directly; multiplying by one
                # million restores TWD scale but does not change rank order.
                value = self._decimal(item.get("MarketValue")) * Decimal("1000000")
                if value > 0:
                    rankings.append(TaiwanMarketRank(
                        symbol=f"{code}.TWO",
                        market_value_twd=value,
                        source="tpex-openapi-market-value",
                    ))
            self._cached = tuple(sorted(
                rankings,
                key=lambda item: (item.market_value_twd, item.symbol),
                reverse=True,
            ))
            self._cached_at = now
            return self._cached
