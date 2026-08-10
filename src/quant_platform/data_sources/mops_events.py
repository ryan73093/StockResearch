from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, time
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quant_platform.domain.entities import PointInTimeDataset, PointInTimeObservation


TAIPEI = ZoneInfo("Asia/Taipei")


class _MopsHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text.append(data.strip())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href.strip())


class MopsEarningsCallProvider:
    """Official TWSE/TPEx disclosures plus MOPS investor-conference detail.

    The OpenAPI endpoints are current daily reports, not historical archives.
    Historical coverage therefore grows from actual download time.  For a
    single company, MOPS' compatibility page enriches the newest conference
    with presentation and webcast links.
    """

    name = "mops"
    listed_endpoint = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
    otc_endpoint = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"
    detail_endpoint = "https://mopsov.twse.com.tw/mops/web/ajax_t100sb07_1"
    detail_page = "https://mopsov.twse.com.tw/mops/web/t100sb07_1"
    conference_terms = ("法人說明會", "法人說明", "法說會", "法說")

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    def fetch(
        self,
        definition: PointInTimeDataset,
        entity_id: str,
        start: datetime,
        end: datetime,
        ingested_at: datetime,
    ) -> list[PointInTimeObservation]:
        requested = entity_id.upper().removesuffix(".TW").removesuffix(".TWO")
        observations: list[PointInTimeObservation] = []
        for market, endpoint in (("上市", self.listed_endpoint), ("上櫃", self.otc_endpoint)):
            for raw in self._request_json(endpoint):
                normalized = self._normalize_disclosure(
                    definition, market, raw, ingested_at
                )
                if normalized is None:
                    continue
                if requested not in {"", "ALL"} and normalized.entity_id != requested:
                    continue
                if start <= normalized.event_time <= end:
                    observations.append(normalized)
        if requested not in {"", "ALL"}:
            detailed = self._request_detail(requested)
            if detailed:
                normalized = self._normalize_detail(
                    definition, requested, detailed, ingested_at
                )
                if normalized is not None and start <= normalized.event_time <= end:
                    observations.append(normalized)
        unique: dict[tuple[str, datetime, str, str], PointInTimeObservation] = {}
        for item in observations:
            unique[(item.entity_id, item.event_time, item.revision_key, item.content_hash)] = item
        return sorted(unique.values(), key=lambda item: (item.event_time, item.entity_id))

    def _request_json(self, endpoint: str) -> list[dict[str, Any]]:
        request = Request(endpoint, headers={"Accept": "application/json", "User-Agent": "QuantResearchPlatform/3.8"})
        with urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8-sig"))
        if not isinstance(payload, list):
            raise RuntimeError("官方重大訊息 API 回傳格式不是清單")
        return [dict(item) for item in payload]

    def _request_detail(self, entity_id: str) -> str:
        form = urlencode({
            "step": "1", "firstin": "true", "off": "1", "TYPEK": "all",
            "co_id": entity_id, "queryName": "co_id", "inpuType": "co_id",
        }).encode()
        request = Request(self.detail_endpoint, data=form, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": self.detail_page,
            "User-Agent": "QuantResearchPlatform/3.8",
        })
        with urlopen(request, timeout=self._timeout) as response:
            return response.read().decode("utf-8", errors="replace")

    def _normalize_disclosure(
        self,
        definition: PointInTimeDataset,
        market: str,
        raw: dict[str, Any],
        ingested_at: datetime,
    ) -> PointInTimeObservation | None:
        subject = self._field(raw, "主旨", "主旨 ")
        description = self._field(raw, "說明")
        if not any(term in f"{subject} {description}" for term in self.conference_terms):
            return None
        entity = self._field(raw, "公司代號", "SecuritiesCompanyCode").strip().upper()
        if not entity:
            return None
        announcement = self._roc_datetime(
            self._field(raw, "發言日期"), self._field(raw, "發言時間")
        )
        if announcement > ingested_at:
            return None
        event_time = self._conference_datetime(
            description, self._field(raw, "事實發生日"), announcement
        )
        location = self._extract_section(
            description, "召開法人說明會之地點", "法人說明會擇要訊息"
        )
        summary = self._extract_section(
            description, "法人說明會擇要訊息", "其他應敘明事項"
        ) or description
        urls = self._urls(description)
        payload = {
            "市場": market,
            "公司代號": entity,
            "公司名稱": self._field(raw, "公司名稱", "CompanyName"),
            "公告時間": announcement.isoformat(),
            "法說會時間": event_time.isoformat(),
            "主旨": subject,
            "地點": location,
            "擇要訊息": summary,
            "符合條款": self._field(raw, "符合條款"),
            "事實發生日": self._field(raw, "事實發生日"),
            "簡報網址": [],
            "影音／相關網址": urls,
            "簡報文件數": 0,
            "影音／相關連結數": len(urls),
            "來源類型": "每日重大訊息",
        }
        return self._observation(
            definition, entity, event_time, announcement, ingested_at, payload,
            f"{entity}:{event_time.isoformat()}:investor-conference",
            self.listed_endpoint if market == "上市" else self.otc_endpoint,
        )

    def _normalize_detail(
        self,
        definition: PointInTimeDataset,
        entity: str,
        html: str,
        ingested_at: datetime,
    ) -> PointInTimeObservation | None:
        parser = _MopsHtmlParser()
        parser.feed(html)
        text = re.sub(r"\s+", " ", unescape(" ".join(parser.text))).strip()
        if "召開法人說明會日期" not in text:
            return None
        date_match = re.search(r"召開法人說明會日期[：:]\s*(\d{3}/\d{1,2}/\d{1,2})", text)
        time_match = re.search(r"時間[：:]\s*(\d{1,2})\s*點\s*(\d{1,2})\s*分", text)
        if not date_match:
            return None
        event_time = self._roc_datetime(
            date_match.group(1),
            f"{int(time_match.group(1)) if time_match else 0:02d}{int(time_match.group(2)) if time_match else 0:02d}00",
        )
        name_match = re.search(r"公司名稱[：:]\s*(.+?)\s+1\s+召開法人說明會日期", text)
        presentations = sorted({
            urljoin(self.detail_page, value)
            for value in parser.links if "/nas/STR/" in value
        })
        related = sorted({
            urljoin(self.detail_page, value) for value in parser.links
            if value.startswith(("http://", "https://")) and "/nas/STR/" not in value
        })
        location = self._extract_section(text, "召開法人說明會地點", "法人說明會擇要訊息")
        summary = self._extract_section(text, "法人說明會擇要訊息", "法人說明會簡報內容")
        payload = {
            "市場": "依公司掛牌市場",
            "公司代號": entity,
            "公司名稱": name_match.group(1).strip() if name_match else "",
            "公告時間": ingested_at.isoformat(),
            "法說會時間": event_time.isoformat(),
            "主旨": "公開資訊觀測站法說會最新紀錄",
            "地點": location,
            "擇要訊息": summary,
            "符合條款": "",
            "事實發生日": date_match.group(1),
            "簡報網址": presentations,
            "影音／相關網址": related,
            "簡報文件數": len(presentations),
            "影音／相關連結數": len(related),
            "來源類型": "MOPS 法說會明細",
        }
        return self._observation(
            definition, entity, event_time, ingested_at, ingested_at, payload,
            f"{entity}:{event_time.isoformat()}:investor-conference",
            self.detail_page,
        )

    @staticmethod
    def _observation(definition, entity, event, available, ingested, payload, key, uri):
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return PointInTimeObservation(
            id=None, dataset_key=definition.dataset_key, entity_id=entity,
            event_time=event.astimezone(UTC), available_time=available.astimezone(UTC),
            ingested_at=ingested.astimezone(UTC), revision_key=key[:240],
            content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            payload_json=canonical, source="mops", source_uri=uri,
        )

    @staticmethod
    def _field(raw: dict[str, Any], *keys: str) -> str:
        stripped = {str(key).strip(): value for key, value in raw.items()}
        for key in keys:
            value = raw.get(key, stripped.get(key.strip()))
            if value not in {None, ""}:
                return str(value).strip()
        return ""

    @classmethod
    def _conference_datetime(cls, description: str, fact_date: str, fallback: datetime) -> datetime:
        date_match = re.search(r"召開法人說明會之日期[：:]\s*(\d{3}/\d{1,2}/\d{1,2})", description)
        clock_match = re.search(r"召開法人說明會之時間[：:]\s*(\d{1,2})\s*時\s*(\d{1,2})\s*分", description)
        raw_date = date_match.group(1) if date_match else fact_date
        if not raw_date:
            return fallback
        raw_clock = (
            f"{int(clock_match.group(1)):02d}{int(clock_match.group(2)):02d}00"
            if clock_match else "000000"
        )
        return cls._roc_datetime(raw_date, raw_clock)

    @staticmethod
    def _roc_datetime(raw_date: str, raw_time: str = "000000") -> datetime:
        digits = re.sub(r"\D", "", raw_date)
        if len(digits) < 7:
            raise ValueError(f"無法解析民國日期：{raw_date}")
        year = int(digits[:-4]) + 1911
        month, day = int(digits[-4:-2]), int(digits[-2:])
        clock = re.sub(r"\D", "", raw_time).zfill(6)[-6:]
        parsed_time = time(int(clock[:2]), int(clock[2:4]), int(clock[4:6]))
        return datetime.combine(date(year, month, day), parsed_time, tzinfo=TAIPEI).astimezone(UTC)

    @staticmethod
    def _extract_section(text: str, start_label: str, end_label: str) -> str:
        pattern = rf"{re.escape(start_label)}[：:]?\s*(.+?)(?={re.escape(end_label)}[：:]?|$)"
        match = re.search(pattern, text, flags=re.S)
        if not match:
            return ""
        value = re.sub(r"\s+", " ", match.group(1)).strip(" ：:\r\n")
        value = re.sub(r"^\d+\.\s*", "", value)
        return re.sub(r"\s+\d+\.$", "", value).strip()

    @staticmethod
    def _urls(text: str) -> list[str]:
        return sorted({value.rstrip(".,;)]}，。；）") for value in re.findall(r"https?://[^\s<>]+", text)})
