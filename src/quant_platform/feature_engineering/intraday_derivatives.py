from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from quant_platform.domain.entities import (
    FeatureDefinition,
    FeatureRevision,
    PointInTimeDataset,
    PointInTimeObservation,
)


TAIPEI = ZoneInfo("Asia/Taipei")
FEATURE_VERSION = "2.0.0"


def _definition(name, family, description, lookback, unit, datasets) -> FeatureDefinition:
    return FeatureDefinition(
        name=name, version=FEATURE_VERSION, family=family, description=description,
        lookback=lookback,
        parameters_json=json.dumps(
            {"unit": unit, "source_datasets": datasets, "point_in_time": True},
            ensure_ascii=False, sort_keys=True,
        ),
    )


INTRADAY_DERIVATIVE_DEFINITIONS = (
    _definition("intraday_return", "盤中價格", "最後一分鐘收盤相對第一分鐘開盤報酬", 1, "%", ["tw_stock_1m"]),
    _definition("intraday_range_pct", "盤中波動", "分鐘最高與最低價差除以第一分鐘開盤", 1, "%", ["tw_stock_1m"]),
    _definition("intraday_realized_volatility", "盤中波動", "分鐘對數報酬標準差乘根號 240", 3, "%", ["tw_stock_1m"]),
    _definition("intraday_vwap_deviation", "盤中量價", "最後收盤相對成交量加權平均價偏離", 1, "%", ["tw_stock_1m"]),
    _definition("intraday_volume_shares", "盤中量價", "分鐘成交股數合計", 1, "股", ["tw_stock_1m"]),
    _definition("intraday_max_minute_volume_share", "盤中流動性", "最大單分鐘量占當日分鐘量比例", 1, "%", ["tw_stock_1m"]),
    _definition("odd_lot_trade_price", "盤後零股", "證交所零股行情成交價", 1, "元／股", ["tw_odd_lot_daily"]),
    _definition("odd_lot_turnover_twd", "盤後零股", "零股成交金額", 1, "元", ["tw_odd_lot_daily"]),
    _definition("odd_lot_volume_shares", "盤後零股", "零股成交股數", 1, "股", ["tw_odd_lot_daily"]),
    _definition("odd_lot_trade_count", "盤後零股", "零股成交筆數", 1, "筆", ["tw_odd_lot_daily"]),
    _definition("odd_lot_average_trade_size", "盤後零股", "零股平均每筆成交股數", 1, "股／筆", ["tw_odd_lot_daily"]),
    _definition("odd_lot_spread_bps", "盤後零股流動性", "最佳賣價減最佳買價除以中間價", 1, "bps", ["tw_odd_lot_daily"]),
    _definition("odd_lot_order_imbalance", "盤後零股流動性", "最佳買賣報價量不平衡", 1, "%", ["tw_odd_lot_daily"]),
    _definition("futures_front_close", "期貨", "近月契約收盤或結算價格", 1, "指數點／元", ["tw_futures_daily"]),
    _definition("futures_front_return_1d", "期貨", "近月契約一日價格報酬", 2, "%", ["tw_futures_daily"]),
    _definition("futures_front_range_pct", "期貨", "近月契約日內高低價差占收盤比例", 1, "%", ["tw_futures_daily"]),
    _definition("futures_volume_contracts", "期貨", "近月契約成交口數", 1, "口", ["tw_futures_daily"]),
    _definition("futures_open_interest_contracts", "期貨", "近月契約未平倉口數", 1, "口", ["tw_futures_daily"]),
    _definition("futures_open_interest_change_1d", "期貨", "近月未平倉口數一日變化率", 2, "%", ["tw_futures_daily"]),
    _definition("option_put_call_volume_ratio", "選擇權", "賣權成交量除以買權成交量", 1, "倍", ["tw_options_daily"]),
    _definition("option_put_call_oi_ratio", "選擇權", "賣權未平倉量除以買權未平倉量", 1, "倍", ["tw_options_daily"]),
    _definition("option_total_volume_contracts", "選擇權", "全部履約價買賣權成交口數", 1, "口", ["tw_options_daily"]),
    _definition("option_active_contract_count", "選擇權", "有效履約價與買賣權契約數", 1, "個契約", ["tw_options_daily"]),
    _definition("option_median_strike", "選擇權", "有效履約價中位數", 1, "指數點／元", ["tw_options_daily"]),
    _definition("tw_option_vix_level", "選擇權風險", "臺指選擇權波動率指數最新值", 1, "%", ["tw_option_vix"]),
    _definition("tw_option_vix_change_1d", "選擇權風險", "臺指選擇權波動率指數一日變化", 2, "點", ["tw_option_vix"]),
)


class IntradayDerivativeFeatureEngine:
    definitions = INTRADAY_DERIVATIVE_DEFINITIONS
    supported_datasets = {
        "tw_stock_1m", "tw_odd_lot_daily", "tw_futures_daily",
        "tw_options_daily", "tw_option_vix",
    }

    def compute(
        self,
        definition: PointInTimeDataset,
        observations: list[PointInTimeObservation],
        symbol_resolver: Callable[[str], str] | None = None,
        computed_at: datetime | None = None,
    ) -> list[FeatureRevision]:
        if definition.dataset_key not in self.supported_datasets or not observations:
            return []
        calculated_at = self._aware(computed_at or datetime.now(UTC))
        resolver = symbol_resolver or (lambda value: value.upper())
        grouped = defaultdict(list)
        for item in observations:
            local_day = self._aware(item.event_time).astimezone(TAIPEI).date()
            grouped[(item.entity_id, local_day)].append(item)
        methods = {
            "tw_stock_1m": self._minute_features,
            "tw_odd_lot_daily": self._odd_lot_features,
            "tw_futures_daily": self._futures_features,
            "tw_options_daily": self._option_features,
            "tw_option_vix": self._vix_features,
        }
        output = []
        for entity, event, available, features, inputs in methods[definition.dataset_key](grouped):
            fingerprint = self._fingerprint(inputs)
            lineage = json.dumps({
                "dataset_key": definition.dataset_key,
                "source_dataset": definition.source_dataset,
                "entity_id": entity,
                "input_count": len(inputs),
                "input_fingerprint": fingerprint,
                "maximum_input_available_time": available.isoformat(),
            }, ensure_ascii=False, sort_keys=True)
            for name, raw in features.items():
                if raw is None or not math.isfinite(float(raw)):
                    continue
                output.append(FeatureRevision(
                    id=None, symbol=resolver(entity), feature_name=name,
                    feature_version=FEATURE_VERSION, event_time=event,
                    available_time=available, computed_at=calculated_at,
                    value=float(raw), input_fingerprint=fingerprint, lineage_json=lineage,
                ))
        return output

    def _minute_features(self, grouped):
        output = []
        for (entity, _), items in sorted(grouped.items()):
            parsed = []
            for item in sorted(items, key=lambda value: value.event_time):
                raw = json.loads(item.payload_json)
                values = (
                    self._number(raw, "open", "Open"), self._number(raw, "high", "max", "High"),
                    self._number(raw, "low", "min", "Low"), self._number(raw, "close", "Close"),
                    max(self._number(raw, "volume", "Volume") or 0.0, 0.0),
                )
                if None not in values[:4]:
                    parsed.append((item, *values))
            if not parsed:
                continue
            closes, volumes = [row[4] for row in parsed], [row[5] for row in parsed]
            total = sum(volumes)
            vwap = sum(price * volume for price, volume in zip(closes, volumes)) / total if total else None
            log_returns = [
                math.log(current / previous)
                for previous, current in zip(closes, closes[1:]) if previous > 0 and current > 0
            ]
            realized = statistics.stdev(log_returns) * math.sqrt(240) if len(log_returns) >= 2 else None
            first_open, last_close = parsed[0][1], parsed[-1][4]
            features = {
                "intraday_return": last_close / first_open - 1 if first_open else None,
                "intraday_range_pct": (max(row[2] for row in parsed) - min(row[3] for row in parsed)) / first_open if first_open else None,
                "intraday_realized_volatility": realized,
                "intraday_vwap_deviation": last_close / vwap - 1 if vwap else None,
                "intraday_volume_shares": total,
                "intraday_max_minute_volume_share": max(volumes) / total if total else None,
            }
            output.append(self._record(entity, features, [row[0] for row in parsed]))
        return output

    def _odd_lot_features(self, grouped):
        output = []
        for (entity, _), items in sorted(grouped.items()):
            for item in items:
                raw = json.loads(item.payload_json)
                price = self._number(raw, "TradePrice", "成交價")
                volume = self._number(raw, "TradeVolume", "成交股數")
                count = self._number(raw, "Transaction", "成交筆數")
                turnover = self._number(raw, "TradeValue", "成交金額")
                bid, ask = self._number(raw, "BestBidPrice"), self._number(raw, "BestAskPrice")
                bid_volume, ask_volume = self._number(raw, "BestBidVolume"), self._number(raw, "BestAskVolume")
                midpoint = (bid + ask) / 2 if bid and ask else None
                quoted = (bid_volume or 0) + (ask_volume or 0)
                output.append(self._record(entity, {
                    "odd_lot_trade_price": price,
                    "odd_lot_turnover_twd": turnover,
                    "odd_lot_volume_shares": volume,
                    "odd_lot_trade_count": count,
                    "odd_lot_average_trade_size": volume / count if volume is not None and count else None,
                    "odd_lot_spread_bps": (ask - bid) / midpoint * 10000 if midpoint else None,
                    "odd_lot_order_imbalance": ((bid_volume or 0) - (ask_volume or 0)) / quoted if quoted else None,
                }, [item]))
        return output

    def _futures_features(self, grouped):
        selected = []
        for (entity, day), items in sorted(grouped.items()):
            current_month = int(day.strftime("%Y%m"))
            candidates = []
            for item in items:
                raw = json.loads(item.payload_json)
                close = self._number(raw, "close", "settlement_price", "Close")
                if close is None:
                    continue
                month_text = str(raw.get("contract_date") or raw.get("contract_month") or current_month)
                digits = "".join(char for char in month_text if char.isdigit())[:6]
                month = int(digits) if len(digits) == 6 else current_month
                session = str(raw.get("trading_session") or raw.get("session") or "position").lower()
                candidates.append((month < current_month, month, int("after" in session or "night" in session), item, raw, close))
            if candidates:
                _, _, _, item, raw, close = min(candidates, key=lambda value: value[:3])
                selected.append((entity, item, close, self._number(raw, "max", "high"),
                                 self._number(raw, "min", "low"), self._number(raw, "volume"),
                                 self._number(raw, "open_interest")))
        output, previous = [], {}
        for entity, item, close, high, low, volume, open_interest in selected:
            prior = previous.get(entity)
            output.append(self._record(entity, {
                "futures_front_close": close,
                "futures_front_return_1d": close / prior[0] - 1 if prior and prior[0] else None,
                "futures_front_range_pct": (high - low) / close if high is not None and low is not None and close else None,
                "futures_volume_contracts": volume,
                "futures_open_interest_contracts": open_interest,
                "futures_open_interest_change_1d": open_interest / prior[1] - 1 if prior and prior[1] and open_interest is not None else None,
            }, [item]))
            previous[entity] = (close, open_interest)
        return output

    def _option_features(self, grouped):
        output = []
        for (entity, _), items in sorted(grouped.items()):
            call_volume = put_volume = call_oi = put_oi = 0.0
            strikes = []
            for item in items:
                raw = json.loads(item.payload_json)
                side = str(raw.get("PutCall") or raw.get("callput") or raw.get("put_call") or "").lower()
                is_put = side in {"p", "put", "賣權"} or "put" in side
                volume, oi = self._number(raw, "volume") or 0.0, self._number(raw, "open_interest") or 0.0
                strike = self._number(raw, "ExercisePrice", "strike_price")
                if strike is not None:
                    strikes.append(strike)
                if is_put:
                    put_volume += volume
                    put_oi += oi
                else:
                    call_volume += volume
                    call_oi += oi
            output.append(self._record(entity, {
                "option_put_call_volume_ratio": put_volume / call_volume if call_volume else None,
                "option_put_call_oi_ratio": put_oi / call_oi if call_oi else None,
                "option_total_volume_contracts": call_volume + put_volume,
                "option_active_contract_count": len(items),
                "option_median_strike": statistics.median(strikes) if strikes else None,
            }, items))
        return output

    def _vix_features(self, grouped):
        selected = []
        for (entity, _), items in sorted(grouped.items()):
            valid = [(item, self._number(json.loads(item.payload_json), "vix")) for item in items]
            valid = [row for row in valid if row[1] is not None]
            if valid:
                selected.append((entity, *max(valid, key=lambda row: row[0].event_time)))
        output, previous = [], {}
        for entity, item, value in selected:
            output.append(self._record(entity, {
                "tw_option_vix_level": value,
                "tw_option_vix_change_1d": value - previous[entity] if entity in previous else None,
            }, [item]))
            previous[entity] = value
        return output

    @classmethod
    def _record(cls, entity, features, inputs):
        return (
            entity, max(cls._aware(item.event_time) for item in inputs),
            max(cls._aware(item.available_time) for item in inputs), features, list(inputs),
        )

    @staticmethod
    def _number(raw, *keys):
        for key in keys:
            value = raw.get(key)
            if value not in {None, "", "-", "--"}:
                try:
                    return float(str(value).replace(",", ""))
                except ValueError:
                    continue
        return None

    @staticmethod
    def _fingerprint(values: Iterable[PointInTimeObservation]) -> str:
        return hashlib.sha256("|".join(sorted(item.content_hash for item in values)).encode()).hexdigest()

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
