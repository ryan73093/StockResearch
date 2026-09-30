from __future__ import annotations

import hashlib
import http.client
import json
import threading
import time as clock
from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quant_platform.domain.entities import TaiwanDataRecord


TAIPEI = ZoneInfo("Asia/Taipei")


class TaiwanOfficialFallbackProvider:
    """Free market-wide official snapshots with FinMind for other datasets."""

    name = "taiwan_official_router"
    continues_after_primary_block = True
    batch_datasets = frozenset({
        "TaiwanStockPER",
        "TaiwanStockInstitutionalInvestorsBuySell",
        "TaiwanStockMarginPurchaseShortSale",
        "TaiwanStockShareholdingDistribution",
    })
    _urls = {
        ("TaiwanStockPER", ".TW"):
            "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
        ("TaiwanStockPER", ".TWO"):
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
        ("TaiwanStockMarginPurchaseShortSale", ".TW"):
            "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN",
        ("TaiwanStockMarginPurchaseShortSale", ".TWO"):
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance",
        ("TaiwanStockInstitutionalInvestorsBuySell", ".TW"):
            "https://www.twse.com.tw/rwd/zh/fund/T86",
        ("TaiwanStockInstitutionalInvestorsBuySell", ".TWO"):
            "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading",
        ("TaiwanStockShareholdingDistribution", "ALL"):
            "https://openapi.tdcc.com.tw/v1/opendata/1-5",
    }

    def __init__(self, fallback: object, timeout: int = 30) -> None:
        self._fallback = fallback
        self._timeout = timeout
        self._lock = threading.RLock()
        self._cache: dict[
            str, tuple[float, dict[str, list[dict[str, Any]]]]
        ] = {}

    def source_for(self, dataset: str) -> str:
        if dataset in {
            "TaiwanStockPER", "TaiwanStockMarginPurchaseShortSale",
            "TaiwanStockInstitutionalInvestorsBuySell",
        }:
            return "twse_tpex_openapi"
        if dataset == "TaiwanStockShareholdingDistribution":
            return "tdcc_openapi"
        return str(getattr(self._fallback, "name", "finmind"))

    def status(self) -> dict[str, Any]:
        fallback_status = getattr(self._fallback, "status", None)
        return {
            "official_market_api": "TWSE/TPEx valuation and margin snapshots",
            "official_shareholding_api": "TDCC weekly distribution snapshot",
            "finmind": fallback_status() if callable(fallback_status) else {},
        }

    def fetch(
        self, dataset: str, symbol: str, start: datetime, end: datetime
    ) -> list[TaiwanDataRecord]:
        if dataset not in self.batch_datasets:
            return self._fallback.fetch(dataset, symbol, start, end)
        suffix = ".TWO" if symbol.upper().endswith(".TWO") else ".TW"
        code = symbol.upper().removesuffix(suffix)
        raws = self._market_snapshot(dataset, suffix).get(code, [])
        if not raws:
            return []
        ingested_at = datetime.now(UTC)
        output: list[TaiwanDataRecord] = []
        for raw in raws:
            event_date = self._event_date(dataset, raw, suffix)
            event_time = datetime.combine(
                event_date, time.min, tzinfo=TAIPEI
            ).astimezone(UTC)
            if dataset == "TaiwanStockShareholdingDistribution":
                days_until_monday = (7 - event_date.weekday()) % 7
                days_until_monday = days_until_monday or 7
                available_time = datetime.combine(
                    event_date + timedelta(days=days_until_monday),
                    time(10), tzinfo=TAIPEI,
                ).astimezone(UTC)
                record_key = f"holding_level:{str(raw.get('持股分級', '')).strip()}"
                fields = self._shareholding_fields(raw)
                source = "tdcc_openapi"
            else:
                available_time = datetime.combine(
                    event_date, time(18), tzinfo=TAIPEI
                ).astimezone(UTC)
                record_key = (
                    "official_daily_valuation"
                    if dataset == "TaiwanStockPER"
                    else "official_daily_margin"
                    if dataset == "TaiwanStockMarginPurchaseShortSale"
                    else "official_daily_institutional"
                )
                fields = (
                    self._normalized_fields(raw, suffix)
                    if dataset == "TaiwanStockPER"
                    else self._margin_fields(raw, suffix)
                    if dataset == "TaiwanStockMarginPurchaseShortSale"
                    else self._institutional_fields(raw, suffix)
                )
                source = "twse_tpex_openapi"
            if available_time > ingested_at:
                continue
            canonical = json.dumps(
                fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            output.append(TaiwanDataRecord(
                id=None, symbol=symbol.upper(), dataset=dataset,
                event_time=event_time, available_time=available_time,
                ingested_at=ingested_at, record_key=record_key,
                content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                fields_json=canonical, source=source,
            ))
        return output

    def fetch_many(
        self, dataset: str, symbols: list[str], start: datetime, end: datetime
    ) -> list[TaiwanDataRecord]:
        """Fetch market-wide official data once, then normalize all requested symbols."""
        if dataset not in self.batch_datasets:
            raise ValueError(f"batch fetch is not supported for {dataset}")
        output: list[TaiwanDataRecord] = []
        for symbol in symbols:
            output.extend(self.fetch(dataset, symbol, start, end))
        return output

    def _market_snapshot(
        self, dataset: str, suffix: str
    ) -> dict[str, list[dict[str, Any]]]:
        cache_key = f"{dataset}:{suffix if dataset != 'TaiwanStockShareholdingDistribution' else 'ALL'}"
        cached = self._cache.get(cache_key)
        if cached and clock.monotonic() - cached[0] < 900:
            return cached[1]
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and clock.monotonic() - cached[0] < 900:
                return cached[1]
            url_key = (
                dataset,
                "ALL" if dataset == "TaiwanStockShareholdingDistribution" else suffix,
            )
            request = Request(
                self._institutional_url(suffix)
                if dataset == "TaiwanStockInstitutionalInvestorsBuySell"
                and suffix == ".TW"
                else self._urls[url_key],
                headers={
                    "Accept": "application/json",
                    "User-Agent": "StockResearch/1.0",
                },
            )
            with urlopen(request, timeout=self._timeout) as response:
                try:
                    body = response.read()
                except http.client.IncompleteRead as exc:
                    body = exc.partial
                payload = json.loads(body.decode("utf-8-sig"))
            if (
                dataset == "TaiwanStockInstitutionalInvestorsBuySell"
                and suffix == ".TW"
            ):
                fields = payload.get("fields", [])
                rows = [dict(zip(fields, values, strict=False)) for values in payload.get("data", [])]
                for row in rows:
                    row["Date"] = payload.get("date")
                payload = rows
            if dataset == "TaiwanStockShareholdingDistribution":
                code_key = "證券代號"
            elif dataset == "TaiwanStockInstitutionalInvestorsBuySell" and suffix == ".TW":
                code_key = "證券代號"
            elif dataset == "TaiwanStockMarginPurchaseShortSale" and suffix == ".TW":
                code_key = "股票代號"
            else:
                code_key = "SecuritiesCompanyCode" if suffix == ".TWO" else "Code"
            indexed: dict[str, list[dict[str, Any]]] = {}
            for item in payload:
                code = str(item.get(code_key, "")).strip()
                if code:
                    indexed.setdefault(code, []).append(dict(item))
            self._cache[cache_key] = (clock.monotonic(), indexed)
            return indexed

    def _institutional_url(self, suffix: str) -> str:
        if suffix != ".TW":
            return self._urls[("TaiwanStockInstitutionalInvestorsBuySell", suffix)]
        # The TWSE valuation snapshot can lag T86 by one session. Both markets
        # share the trading calendar, so prefer TPEx's explicitly dated current
        # snapshot and fall back to TWSE only when it is unavailable.
        valuation = self._market_snapshot("TaiwanStockPER", ".TWO")
        if not valuation:
            valuation = self._market_snapshot("TaiwanStockPER", ".TW")
        first = next(iter(valuation.values()), [])
        if not first:
            raise ValueError("official TWSE valuation snapshot has no date")
        raw_date = self._date(first[0].get("Date")).strftime("%Y%m%d")
        return (
            self._urls[("TaiwanStockInstitutionalInvestorsBuySell", ".TW")]
            + f"?date={raw_date}&selectType=ALL&response=json"
        )

    def _event_date(
        self, dataset: str, raw: dict[str, Any], suffix: str
    ):
        if dataset == "TaiwanStockShareholdingDistribution":
            return self._date(raw.get("資料日期") or raw.get("\ufeff資料日期"))
        if raw.get("Date"):
            return self._date(raw["Date"])
        valuation = self._market_snapshot("TaiwanStockPER", ".TWO")
        if not valuation:
            valuation = self._market_snapshot("TaiwanStockPER", suffix)
        first = next(iter(valuation.values()), [])
        if not first:
            raise ValueError("official TWSE snapshot is missing a publication date")
        return self._date(first[0].get("Date"))

    @staticmethod
    def _date(value: object):
        digits = str(value or "").replace("/", "").replace("-", "")
        if len(digits) == 7:
            year = int(digits[:3]) + 1911
            month, day = int(digits[3:5]), int(digits[5:7])
        elif len(digits) == 8:
            year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        else:
            raise ValueError(f"unsupported official market date: {value!r}")
        return datetime(year, month, day).date()

    @staticmethod
    def _normalized_fields(raw: dict[str, Any], suffix: str) -> dict[str, Any]:
        if suffix == ".TWO":
            return {
                "date": raw.get("Date"),
                "stock_id": raw.get("SecuritiesCompanyCode"),
                "PER": raw.get("PriceEarningRatio"),
                "PBR": raw.get("PriceBookRatio"),
                "dividend_yield": raw.get("YieldRatio"),
            }
        return {
            "date": raw.get("Date"),
            "stock_id": raw.get("Code"),
            "PER": raw.get("PEratio"),
            "PBR": raw.get("PBratio"),
            "dividend_yield": raw.get("DividendYield"),
        }

    @staticmethod
    def _margin_fields(raw: dict[str, Any], suffix: str) -> dict[str, Any]:
        if suffix == ".TWO":
            return {
                "date": raw.get("Date"),
                "stock_id": raw.get("SecuritiesCompanyCode"),
                "MarginPurchaseTodayBalance": raw.get("MarginPurchaseBalance"),
                "ShortSaleTodayBalance": raw.get("ShortSaleBalance"),
                "MarginPurchaseUtilizationRate": raw.get("MarginPurchaseUtilizationRate"),
                "ShortSaleUtilizationRate": raw.get("ShortSaleUtilizationRate"),
            }
        return {
            "stock_id": raw.get("股票代號"),
            "MarginPurchaseTodayBalance": raw.get("融資今日餘額"),
            "ShortSaleTodayBalance": raw.get("融券今日餘額"),
            "MarginPurchaseBuy": raw.get("融資買進"),
            "MarginPurchaseSell": raw.get("融資賣出"),
            "ShortSaleBuy": raw.get("融券買進"),
            "ShortSaleSell": raw.get("融券賣出"),
        }

    @staticmethod
    def _shareholding_fields(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "date": raw.get("資料日期") or raw.get("\ufeff資料日期"),
            "stock_id": str(raw.get("證券代號", "")).strip(),
            "holding_level": raw.get("持股分級"),
            "holders": raw.get("人數"),
            "shares": raw.get("股數"),
            "ratio_pct": raw.get("占集保庫存數比例%"),
        }

    @staticmethod
    def _institutional_fields(raw: dict[str, Any], suffix: str) -> dict[str, Any]:
        if suffix == ".TWO":
            foreign = raw.get(
                "ForeignInvestorsInclude MainlandAreaInvestors-Difference"
            )
            trust = raw.get("SecuritiesInvestmentTrustCompanies-Difference")
            dealer = raw.get("Dealers-Difference")
            total = raw.get("TotalDifference")
            return {
                "date": raw.get("Date"),
                "stock_id": raw.get("SecuritiesCompanyCode"),
                "foreign_net_buy": foreign,
                "investment_trust_net_buy": trust,
                "dealer_net_buy": dealer,
                "institutional_net_buy": total,
            }
        return {
            "date": raw.get("Date"),
            "stock_id": raw.get("證券代號"),
            "foreign_net_buy": raw.get(
                "外陸資買賣超股數(不含外資自營商)"
            ),
            "investment_trust_net_buy": raw.get("投信買賣超股數"),
            "dealer_net_buy": raw.get("自營商買賣超股數"),
            "institutional_net_buy": raw.get("三大法人買賣超股數"),
        }
