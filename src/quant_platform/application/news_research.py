from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

from quant_platform.application.ports import TaiwanDataRepository


@dataclass(frozen=True, slots=True)
class NewsItem:
    symbol: str
    event_time: datetime
    available_time: datetime
    title: str
    summary: str
    source_name: str
    url: str
    sentiment: float
    sentiment_label: str


@dataclass(frozen=True, slots=True)
class NewsResearchOverview:
    items: tuple[NewsItem, ...]
    symbol: str | None
    total_count: int
    positive_count: int
    neutral_count: int
    negative_count: int
    latest_available_time: datetime | None
    daily_dates: tuple[str, ...]
    daily_sentiment: tuple[float, ...]
    limitations: tuple[str, ...]


class NewsResearchService:
    POSITIVE = ("成長", "創高", "上調", "獲利", "擴產", "利多", "優於", "突破", "growth", "upgrade")
    NEGATIVE = ("衰退", "下修", "虧損", "裁員", "利空", "低於", "違約", "調查", "跌停", "downgrade")

    def __init__(self, repository: TaiwanDataRepository) -> None:
        self._repository = repository

    def get_overview(self, symbol: str | None = None, limit: int = 100) -> NewsResearchOverview:
        normalized = self._normalize_symbol(symbol) if symbol else None
        rows = self._repository.list_records(dataset="TaiwanStockNews", symbol=normalized)
        latest: dict[tuple[str, datetime, str], object] = {}
        for row in rows:
            key = (row.symbol, row.event_time, row.record_key)
            previous = latest.get(key)
            if previous is None or row.ingested_at > previous.ingested_at:
                latest[key] = row
        items = [self._to_item(row) for row in latest.values()]
        items.sort(key=lambda item: (item.event_time, item.available_time), reverse=True)
        daily: dict[date, list[float]] = {}
        for item in items:
            daily.setdefault(item.event_time.date(), []).append(item.sentiment)
        dates = sorted(daily)[-60:]
        displayed = tuple(items[: max(1, min(limit, 10_000))])
        return NewsResearchOverview(
            items=displayed, symbol=normalized, total_count=len(items),
            positive_count=sum(item.sentiment > 0.15 for item in items),
            neutral_count=sum(-0.15 <= item.sentiment <= 0.15 for item in items),
            negative_count=sum(item.sentiment < -0.15 for item in items),
            latest_available_time=max((item.available_time for item in items), default=None),
            daily_dates=tuple(item.isoformat() for item in dates),
            daily_sentiment=tuple(sum(daily[item]) / len(daily[item]) for item in dates),
            limitations=(
                "目前情緒分數是可稽核的詞典基線，不是大型語言模型判讀。",
                "新聞可能重複、延遲或缺少全文，不能單獨作為買賣訊號。",
                "正式候選策略必須驗證新聞發佈時間、交易成本與樣本外增益。",
            ),
        )

    def _to_item(self, row) -> NewsItem:
        raw = json.loads(row.fields_json)
        title = str(raw.get("title") or raw.get("news_title") or "未命名新聞")
        summary = str(raw.get("description") or raw.get("summary") or "")
        text = f"{title} {summary}".lower()
        score = sum(text.count(word) for word in self.POSITIVE) - sum(
            text.count(word) for word in self.NEGATIVE
        )
        sentiment = max(-1.0, min(1.0, score / 3))
        label = "正向" if sentiment > 0.15 else "負向" if sentiment < -0.15 else "中性"
        return NewsItem(
            symbol=row.symbol, event_time=row.event_time, available_time=row.available_time,
            title=title, summary=summary[:360],
            source_name=str(raw.get("source") or raw.get("publisher") or row.source),
            url=str(raw.get("link") or raw.get("url") or ""),
            sentiment=sentiment, sentiment_label=label,
        )

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        value = symbol.strip().upper()
        return value if "." in value else f"{value}.TW"
