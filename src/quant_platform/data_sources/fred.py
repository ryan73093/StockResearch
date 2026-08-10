from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime, time, timedelta
from urllib.parse import urlencode

import requests

from quant_platform.domain.entities import MacroObservation


class FredCsvProvider:
    name = "fred"
    _lags = {"CPIAUCSL": 20, "FEDFUNDS": 20, "UNRATE": 10, "GDP": 35, "DGS10": 1, "DGS2": 1, "T10Y2Y": 1}

    def __init__(self, base_url: str = "https://fred.stlouisfed.org/graph/fredgraph.csv", timeout: int = 30) -> None:
        self._base_url, self._timeout = base_url, timeout

    def fetch(self, series_id: str, start: datetime, end: datetime) -> list[MacroObservation]:
        url = f"{self._base_url}?{urlencode({'id': series_id, 'cosd': start.date().isoformat(), 'coed': end.date().isoformat()})}"
        response = requests.get(url, timeout=self._timeout)
        response.raise_for_status()
        content = response.content.decode("utf-8-sig")
        ingested_at = datetime.now(UTC)
        output: list[MacroObservation] = []
        for row in csv.DictReader(io.StringIO(content)):
            raw = row.get(series_id)
            if raw in {None, "", "."}:
                continue
            date_text = row.get("observation_date") or row.get("DATE")
            if not date_text:
                continue
            event_date = datetime.strptime(date_text, "%Y-%m-%d").date()
            event_time = datetime.combine(event_date, time.min, tzinfo=UTC)
            available_time = datetime.combine(event_date + timedelta(days=self._lags[series_id]), time(21), tzinfo=UTC)
            value = float(raw)
            if available_time <= ingested_at:
                output.append(MacroObservation(None, series_id, event_time, available_time, ingested_at, value, hashlib.sha256(f"{series_id}|{event_date}|{value}".encode()).hexdigest(), self.name))
        return output
