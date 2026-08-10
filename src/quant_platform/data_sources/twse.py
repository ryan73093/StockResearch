from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.request import Request, urlopen


INDUSTRY_NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
    "15": "航運業", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨",
    "19": "綜合", "20": "其他", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業",
    "26": "光電業", "27": "通信網路業", "28": "電子零組件業",
    "29": "電子通路業", "30": "資訊服務業", "31": "其他電子業",
    "32": "文化創意業", "33": "農業科技業", "34": "電子商務業",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
}


@dataclass(frozen=True, slots=True)
class TaiwanCompanyProfile:
    symbol: str
    company_name: str
    company_abbreviation: str
    industry_code: str
    sector: str
    paid_in_capital: int | None
    issued_shares: int | None
    source: str


class TwseCompanyProvider:
    """Official TWSE adapter for current listed-company identity and listing dates."""

    name = "twse-openapi"

    def __init__(
        self,
        company_url: str = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        tpex_company_url: str = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
        timeout: int = 30,
    ) -> None:
        self._company_url = company_url
        self._tpex_company_url = tpex_company_url
        self._timeout = timeout

    def _fetch(self, url: str) -> list[dict[str, Any]]:
        request = Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "QuantResearchPlatform/2.0",
        })
        with urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8-sig"))
        if not isinstance(payload, list):
            raise RuntimeError("Taiwan company endpoint returned an unexpected payload")
        return [dict(item) for item in payload]

    def fetch_listed_companies(self) -> list[dict[str, Any]]:
        return self._fetch(self._company_url)

    @staticmethod
    def _integer(value: object) -> int | None:
        normalized = str(value or "").replace(",", "").strip()
        try:
            parsed = int(normalized)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    def fetch_company_profiles(self) -> tuple[TaiwanCompanyProfile, ...]:
        output: list[TaiwanCompanyProfile] = []
        for row in self._fetch(self._company_url):
            code = str(row.get("公司代號") or "").strip()
            if not code:
                continue
            industry_code = str(row.get("產業別") or "").strip().zfill(2)
            output.append(TaiwanCompanyProfile(
                symbol=f"{code}.TW",
                company_name=str(row.get("公司名稱") or "").strip(),
                company_abbreviation=str(row.get("公司簡稱") or "").strip(),
                industry_code=industry_code,
                sector=INDUSTRY_NAMES.get(industry_code, f"產業代碼 {industry_code}"),
                paid_in_capital=self._integer(row.get("實收資本額")),
                issued_shares=self._integer(
                    row.get("已發行普通股數或TDR原股發行股數")
                ),
                source="twse-openapi-company-profile",
            ))
        for row in self._fetch(self._tpex_company_url):
            code = str(row.get("SecuritiesCompanyCode") or "").strip()
            if not code:
                continue
            industry_code = str(row.get("SecuritiesIndustryCode") or "").strip().zfill(2)
            output.append(TaiwanCompanyProfile(
                symbol=f"{code}.TWO",
                company_name=str(row.get("CompanyName") or "").strip(),
                company_abbreviation=str(row.get("CompanyAbbreviation") or "").strip(),
                industry_code=industry_code,
                sector=INDUSTRY_NAMES.get(industry_code, f"產業代碼 {industry_code}"),
                paid_in_capital=self._integer(row.get("Paidin.Capital.NTDollars")),
                issued_shares=self._integer(row.get("IssueShares")),
                source="tpex-openapi-company-profile",
            ))
        return tuple(output)

    @staticmethod
    def company_code(row: dict[str, Any]) -> str:
        return str(row.get("公司代號") or row.get("Code") or "").strip()

    @staticmethod
    def listing_date(row: dict[str, Any]) -> date | None:
        raw = str(row.get("上市日期") or row.get("ListingDate") or "").strip()
        if not raw:
            return None
        normalized = raw.replace(".", "/").replace("-", "/")
        parts = normalized.split("/")
        if len(parts) == 3 and all(item.isdigit() for item in parts):
            year, month, day = map(int, parts)
            if year < 1911:
                year += 1911
            return date(year, month, day)
        for pattern in ("%Y%m%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw, pattern).date()
            except ValueError:
                continue
        return None
