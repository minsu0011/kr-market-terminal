from __future__ import annotations

import asyncio
import hashlib
import html
import io
import json
import logging
import math
import re
import zipfile
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .config import STOCK_BY_SYMBOL, STOCKS, Settings, provider_secret_verified
from .database import Database
from .feature_providers import ProviderStatus

UTC = timezone.utc
KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)
TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "nclick", "campaign", "source",
}
PRIMARY_DOMAINS = {
    "dart.fss.or.kr", "opendart.fss.or.kr", "kind.krx.co.kr",
    "fsc.go.kr", "fss.or.kr", "korea.kr",
}
AGENCY_NAMES = ("연합뉴스", "뉴스1", "뉴시스", "로이터", "Reuters", "AP")
POSITIVE_WORDS = (
    "수주", "계약", "승인", "흑자", "증가", "확대", "개선", "돌파",
    "신제품", "투자", "자사주", "배당", "성장", "공급",
)
NEGATIVE_WORDS = (
    "철회", "해지", "적자", "감소", "리콜", "사고", "소송", "제재",
    "유상증자", "희석", "하향", "중단", "압수수색", "과징금", "부도",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_time(value: str | datetime | None, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            try:
                return parsedate_to_datetime(str(value)).astimezone(UTC)
            except (TypeError, ValueError, OverflowError):
                pass
    return fallback or utcnow()


def clean_text(value: str | None) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


def canonicalize_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        query = [
            (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_KEYS and not key.lower().startswith("utm_")
        ]
        host = (parsed.hostname or "").lower().removeprefix("www.")
        port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
        path = re.sub(r"/+", "/", parsed.path).rstrip("/") or "/"
        return urlunsplit((parsed.scheme.lower() or "https", host + port, path, urlencode(query), ""))
    except (ValueError, UnicodeError):
        return value.strip()


def domain_of(value: str) -> str:
    try:
        return (urlsplit(value).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def normalized_tokens(value: str) -> set[str]:
    text = clean_text(value).lower()
    text = re.sub(r"\[[^\]]+\]|\([^)]+\)", " ", text)
    text = re.sub(r"(속보|단독|종합|공식|영상|포토)", " ", text)
    text = re.sub(r"\d+(?:[.,]\d+)*", "<num>", text)
    return {token for token in re.findall(r"[가-힣a-z0-9<>]{2,}", text) if token}


def text_fingerprint(value: str) -> str:
    normalized = " ".join(sorted(normalized_tokens(value)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


@dataclass(slots=True)
class RawNewsItem:
    article_id: str
    provider_id: str
    publisher: str
    publisher_domain: str
    original_publisher: str | None
    title: str
    summary: str | None
    body: str | None
    url: str
    published_at: str
    received_at: str
    language: str = "ko"
    symbols_explicit: list[str] = field(default_factory=list)
    company_names_explicit: list[str] = field(default_factory=list)
    is_press_release: bool = False
    is_disclosure: bool = False
    is_correction: bool = False
    is_retraction: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], provider_id: str) -> "RawNewsItem":
        now = utcnow().isoformat()
        url = canonicalize_url(str(raw.get("url") or raw.get("link") or ""))
        title = clean_text(str(raw.get("title") or ""))
        article_id = str(raw.get("article_id") or hashlib.sha256(
            f"{provider_id}|{url}|{title}|{raw.get('published_at')}".encode("utf-8")
        ).hexdigest()[:24])
        return cls(
            article_id=article_id,
            provider_id=provider_id,
            publisher=clean_text(str(raw.get("publisher") or domain_of(url) or provider_id)),
            publisher_domain=str(raw.get("publisher_domain") or domain_of(url)),
            original_publisher=clean_text(raw.get("original_publisher")) or None,
            title=title,
            summary=clean_text(raw.get("summary")) or None,
            body=clean_text(raw.get("body")) or None,
            url=url,
            published_at=parse_time(raw.get("published_at")).isoformat(),
            received_at=parse_time(raw.get("received_at"), utcnow()).isoformat(),
            language=str(raw.get("language") or "ko"),
            symbols_explicit=[str(value) for value in raw.get("symbols_explicit", [])],
            company_names_explicit=[str(value) for value in raw.get("company_names_explicit", [])],
            is_press_release=bool(raw.get("is_press_release")),
            is_disclosure=bool(raw.get("is_disclosure")),
            is_correction=bool(raw.get("is_correction")),
            is_retraction=bool(raw.get("is_retraction")),
            metadata=dict(raw.get("metadata") or {}),
        )


class NewsProvider(ABC):
    provider_id: str

    @abstractmethod
    async def health_check(self) -> ProviderStatus: ...

    @abstractmethod
    async def fetch_latest(
        self, symbols: list[str], since: datetime, limit: int
    ) -> list[RawNewsItem]: ...

    async def fetch_article(self, article_id: str) -> RawNewsItem:
        raise LookupError(article_id)


class UnavailableNewsProvider(NewsProvider):
    def __init__(self, provider_id: str, reason: str):
        self.provider_id = provider_id
        self.reason = reason

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(self.provider_id, False, "UNAVAILABLE", self.reason)

    async def fetch_latest(self, symbols: list[str], since: datetime, limit: int) -> list[RawNewsItem]:
        return []


class JsonNewsProvider(NewsProvider):
    def __init__(self, provider_id: str, path: Path, enabled: bool, state: str):
        self.provider_id = provider_id
        self.path = path
        self.enabled = enabled
        self.state = state

    async def health_check(self) -> ProviderStatus:
        if not self.enabled:
            return ProviderStatus(self.provider_id, False, "UNAVAILABLE", "현재 모드에서 비활성")
        if not self.path.exists():
            return ProviderStatus(self.provider_id, False, "UNAVAILABLE", f"{self.path.name} 없음")
        return ProviderStatus(self.provider_id, True, self.state, f"{self.path.name} 연결")

    async def fetch_latest(self, symbols: list[str], since: datetime, limit: int) -> list[RawNewsItem]:
        if not self.enabled or not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            rows = payload.get("items", payload) if isinstance(payload, dict) else payload
            normalized_rows = []
            for original in rows:
                row = dict(original)
                if self.provider_id == "fixture_news" and "published_offset_minutes" in row:
                    row["published_at"] = (
                        utcnow() - timedelta(minutes=float(row["published_offset_minutes"]))
                    ).isoformat()
                    row["received_at"] = utcnow().isoformat()
                normalized_rows.append(row)
            result = [RawNewsItem.from_dict(row, self.provider_id) for row in normalized_rows]
            wanted = set(symbols)
            return [
                item for item in result
                if parse_time(item.published_at) >= since and (not item.symbols_explicit or wanted & set(item.symbols_explicit))
            ][:limit]
        except (OSError, ValueError, TypeError):
            return []


class NaverNewsProvider(NewsProvider):
    provider_id = "naver_news"
    endpoint = "https://openapi.naver.com/v1/search/news.json"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    async def health_check(self) -> ProviderStatus:
        available = bool(self.client_id and self.client_secret)
        return ProviderStatus(
            self.provider_id, available, "NEAR_REALTIME" if available else "UNAVAILABLE",
            "네이버 뉴스 검색 연결 준비" if available else "NAVER_CLIENT_ID/SECRET 미설정",
        )

    async def fetch_latest(self, symbols: list[str], since: datetime, limit: int) -> list[RawNewsItem]:
        if not self.client_id or not self.client_secret:
            return []
        return await asyncio.to_thread(self._fetch_sync, symbols, since, limit)

    def _fetch_sync(self, symbols: list[str], since: datetime, limit: int) -> list[RawNewsItem]:
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }
        per_symbol = max(5, min(20, math.ceil(limit / max(len(symbols), 1))))
        result: list[RawNewsItem] = []
        for symbol in symbols:
            name = STOCK_BY_SYMBOL.get(symbol, {}).get("name", symbol)
            response = requests.get(
                self.endpoint,
                headers=headers,
                params={"query": name, "display": per_symbol, "start": 1, "sort": "date"},
                timeout=12,
            )
            response.raise_for_status()
            for row in response.json().get("items", []):
                title = clean_text(row.get("title"))
                summary = clean_text(row.get("description"))
                if name.lower() not in f"{title} {summary}".lower():
                    continue
                published = parse_time(row.get("pubDate"))
                if published < since:
                    continue
                original = canonicalize_url(str(row.get("originallink") or row.get("link") or ""))
                publisher_domain = domain_of(original)
                agency = next((value for value in AGENCY_NAMES if value.lower() in f"{title} {summary}".lower()), None)
                result.append(RawNewsItem.from_dict({
                    "provider_id": self.provider_id,
                    "publisher": publisher_domain or "네이버 뉴스 검색",
                    "publisher_domain": publisher_domain,
                    "original_publisher": agency,
                    "title": title,
                    "summary": summary,
                    "body": None,
                    "url": original,
                    "published_at": published.isoformat(),
                    "received_at": utcnow().isoformat(),
                    "symbols_explicit": [symbol],
                    "company_names_explicit": [name],
                    "metadata": {"body_status": "UNAVAILABLE", "naver_link": row.get("link")},
                }, self.provider_id))
        unique = {item.article_id: item for item in result}
        return sorted(unique.values(), key=lambda item: item.published_at, reverse=True)[:limit]


class NaverFinanceNewsProvider(NewsProvider):
    """No-key Naver Finance stock-news feed.

    The feed contains a title and a shortened summary, not a verified full
    article body.  Each media office is kept as a separate candidate source;
    the existing copy/wire/press-release union logic then prevents syndicated
    copies from being counted as independent verification.
    """

    provider_id = "naver_finance_news"
    endpoint = "https://m.stock.naver.com/api/news/stock/{symbol}"

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            self.provider_id, True, "NEAR_REALTIME_NO_KEY",
            "네이버 금융 종목뉴스 무키 연결 준비",
        )

    async def fetch_latest(self, symbols: list[str], since: datetime, limit: int) -> list[RawNewsItem]:
        return await asyncio.to_thread(self._fetch_sync, symbols, since, limit)

    def _fetch_sync(self, symbols: list[str], since: datetime, limit: int) -> list[RawNewsItem]:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Referer": "https://m.stock.naver.com/",
        })
        per_symbol = max(5, min(12, math.ceil(limit / max(len(symbols), 1))))
        result: list[RawNewsItem] = []
        for symbol in symbols:
            name = STOCK_BY_SYMBOL.get(symbol, {}).get("name", symbol)
            response = session.get(
                self.endpoint.format(symbol=symbol),
                params={"page": 1, "pageSize": per_symbol}, timeout=12,
            )
            response.raise_for_status()
            groups = response.json()
            if isinstance(groups, dict):
                groups = [groups]
            for group in groups or []:
                for row in group.get("items", []) or []:
                    raw_stamp = str(row.get("datetime") or "")
                    try:
                        published = datetime.strptime(raw_stamp, "%Y%m%d%H%M").replace(tzinfo=KST).astimezone(UTC)
                    except ValueError:
                        published = utcnow()
                    if published < since:
                        continue
                    title = clean_text(row.get("titleFull") or row.get("title"))
                    summary = clean_text(row.get("body"))
                    office_id = str(row.get("officeId") or "unknown")
                    office_name = clean_text(row.get("officeName")) or "네이버 뉴스 제휴매체"
                    agency = next(
                        (value for value in AGENCY_NAMES if value.lower() in f"{title} {summary}".lower()),
                        None,
                    )
                    result.append(RawNewsItem.from_dict({
                        "article_id": f"naver:{office_id}:{row.get('articleId') or row.get('id')}",
                        "publisher": office_name,
                        "publisher_domain": f"naver-news-office:{office_id}",
                        "original_publisher": agency,
                        "title": title,
                        "summary": summary,
                        "body": None,
                        "url": row.get("mobileNewsUrl") or "",
                        "published_at": published.isoformat(),
                        "received_at": utcnow().isoformat(),
                        "symbols_explicit": [symbol],
                        "company_names_explicit": [name],
                        "metadata": {
                            "body_status": "SUMMARY_ONLY",
                            "office_id": office_id,
                            "article_id": row.get("articleId"),
                            "source_identity": office_name,
                        },
                    }, self.provider_id))
        unique = {item.article_id: item for item in result}
        return sorted(unique.values(), key=lambda item: item.published_at, reverse=True)[:limit]


class DartRssNewsProvider(NewsProvider):
    """Official DART today feed that does not require an OpenDART key."""

    provider_id = "dart_rss"
    endpoint = "https://dart.fss.or.kr/api/todayRSS.xml"

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(self.provider_id, True, "PRIMARY_SOURCE_NO_KEY", "DART 공식 오늘 공시 RSS 무키 연결 준비")

    async def fetch_latest(self, symbols: list[str], since: datetime, limit: int) -> list[RawNewsItem]:
        return await asyncio.to_thread(self._fetch_sync, set(symbols), since, limit)

    def _fetch_sync(self, symbols: set[str], since: datetime, limit: int) -> list[RawNewsItem]:
        response = requests.get(
            self.endpoint,
            headers={"User-Agent": "Mozilla/5.0 Chrome/126", "Referer": "https://dart.fss.or.kr/"},
            timeout=15,
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        output: list[RawNewsItem] = []
        for node in root.findall("./channel/item"):
            title = clean_text(node.findtext("title"))
            creator = clean_text(node.findtext("{http://purl.org/dc/elements/1.1/}creator"))
            matched = [
                symbol for symbol in symbols
                if STOCK_BY_SYMBOL.get(symbol, {}).get("name", symbol).lower() in f"{creator} {title}".lower()
            ]
            if not matched:
                continue
            published = parse_time(
                node.findtext("{http://purl.org/dc/elements/1.1/}date") or node.findtext("pubDate")
            )
            if published < since:
                continue
            link = clean_text(node.findtext("link"))
            receipt_match = re.search(r"rcpNo=(\d+)", link)
            receipt = receipt_match.group(1) if receipt_match else text_fingerprint(link or title)
            output.append(RawNewsItem.from_dict({
                "article_id": f"dart:{receipt}",
                "publisher": "금융감독원 전자공시",
                "publisher_domain": "dart.fss.or.kr",
                "original_publisher": "DART",
                "title": title,
                "summary": f"{creator or STOCK_BY_SYMBOL.get(matched[0], {}).get('name', matched[0])} 공식 전자공시",
                "body": title,
                "url": link,
                "published_at": published.isoformat(),
                "received_at": utcnow().isoformat(),
                "symbols_explicit": matched,
                "company_names_explicit": [creator] if creator else [],
                "is_disclosure": True,
                "is_correction": "정정" in title,
                "is_retraction": "철회" in title or "취소" in title,
                "metadata": {"receipt_no": receipt, "feed": "DART todayRSS"},
            }, self.provider_id))
        return output[:limit]


class OpenDartNewsProvider(NewsProvider):
    provider_id = "open_dart_news"
    endpoint = "https://opendart.fss.or.kr/api/list.json"
    corp_code_endpoint = "https://opendart.fss.or.kr/api/corpCode.xml"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._corp_codes: dict[str, str] = {}

    async def health_check(self) -> ProviderStatus:
        available = bool(self.api_key)
        return ProviderStatus(
            self.provider_id, available, "PRIMARY_SOURCE" if available else "UNAVAILABLE",
            "OpenDART 공시검색 연결 준비" if available else "OPEN_DART_API_KEY 미설정",
        )

    async def fetch_latest(self, symbols: list[str], since: datetime, limit: int) -> list[RawNewsItem]:
        if not self.api_key:
            return []
        return await asyncio.to_thread(self._fetch_sync, set(symbols), since, limit)

    def _load_corp_codes(self) -> dict[str, str]:
        if self._corp_codes:
            return self._corp_codes
        response = requests.get(
            self.corp_code_endpoint, params={"crtfc_key": self.api_key}, timeout=20
        )
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            xml_name = next(name for name in archive.namelist() if name.lower().endswith(".xml"))
            root = ElementTree.fromstring(archive.read(xml_name))
        self._corp_codes = {
            clean_text(node.findtext("stock_code")): clean_text(node.findtext("corp_code"))
            for node in root.findall("list")
            if clean_text(node.findtext("stock_code")) and clean_text(node.findtext("corp_code"))
        }
        return self._corp_codes

    def _fetch_sync(self, symbols: set[str], since: datetime, limit: int) -> list[RawNewsItem]:
        end = utcnow()
        corp_codes = self._load_corp_codes()
        items: list[RawNewsItem] = []
        per_symbol = max(5, min(20, math.ceil(limit / max(len(symbols), 1))))
        for requested_symbol in sorted(symbols):
            corp_code = corp_codes.get(requested_symbol)
            if not corp_code:
                continue
            response = requests.get(
                self.endpoint,
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bgn_de": since.strftime("%Y%m%d"),
                    "end_de": end.strftime("%Y%m%d"),
                    "sort": "date",
                    "sort_mth": "desc",
                    "page_count": per_symbol,
                },
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") not in {None, "000", "013"}:
                raise RuntimeError(f"OpenDART 오류 {payload.get('status')}")
            for row in payload.get("list", []) or []:
                symbol = str(row.get("stock_code") or requested_symbol).strip()
                if symbol not in symbols:
                    continue
                receipt = str(row.get("rcept_no") or "")
                report = clean_text(str(row.get("report_nm") or ""))
                date = str(row.get("rcept_dt") or "")
                published = parse_time(f"{date[:4]}-{date[4:6]}-{date[6:]}T00:00:00+09:00") if len(date) == 8 else end
                items.append(RawNewsItem.from_dict({
                    "article_id": f"dart:{receipt}",
                    "publisher": "금융감독원 전자공시",
                    "publisher_domain": "dart.fss.or.kr",
                    "original_publisher": "OpenDART",
                    "title": report,
                    "summary": f"{STOCK_BY_SYMBOL.get(symbol, {}).get('name', symbol)} 공식 전자공시",
                    "body": report,
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                    "published_at": published.isoformat(),
                    "received_at": end.isoformat(),
                    "symbols_explicit": [symbol],
                    "company_names_explicit": [str(row.get("corp_name") or "")],
                    "is_disclosure": True,
                    "is_correction": "정정" in report,
                    "is_retraction": "철회" in report,
                    "metadata": {"receipt_no": receipt, "corp_code": row.get("corp_code")},
                }, self.provider_id))
        return items[:limit]


class CompanyEntityResolver:
    def __init__(self):
        self.aliases: dict[str, set[str]] = {
            stock["symbol"]: {stock["name"], stock["symbol"]} for stock in STOCKS
        }
        self.aliases["005930"].update({"삼성전자", "Samsung Electronics", "갤럭시"})
        self.aliases["000660"].update({"SK하이닉스", "에스케이하이닉스", "SK hynix"})
        self.aliases["005380"].update({"현대차", "현대자동차", "Hyundai Motor"})
        self.aliases["000270"].update({"기아", "Kia"})
        self.aliases["373220"].update({"LG에너지솔루션", "엘지에너지솔루션", "LG엔솔"})
        self.aliases["035420"].update({"NAVER", "네이버"})

    def resolve(self, item: RawNewsItem) -> list[dict[str, Any]]:
        text = f"{item.title} {item.summary or ''} {item.body or ''}".lower()
        links: dict[str, dict[str, Any]] = {}
        for symbol in item.symbols_explicit:
            if symbol in STOCK_BY_SYMBOL:
                links[symbol] = {"symbol": symbol, "relationship": "DIRECT_COMPANY", "confidence": 1.0}
        for symbol, aliases in self.aliases.items():
            matched = next((alias for alias in aliases if alias.lower() in text), None)
            if matched and symbol not in links:
                links[symbol] = {
                    "symbol": symbol, "relationship": "DIRECT_COMPANY",
                    "confidence": 0.92, "matched_alias": matched,
                }
        return list(links.values())


class FactExtractor:
    amount_pattern = re.compile(r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>조|억|만)?\s*원")

    @classmethod
    def event_type(cls, text: str) -> str:
        rules = [
            ("CONTRACT", ("수주", "공급계약", "판매계약")),
            ("INVESTMENT", ("투자", "시설투자", "공장")),
            ("EARNINGS", ("실적", "영업이익", "매출")),
            ("REGULATION", ("규제", "제재", "과징금")),
            ("APPROVAL", ("승인", "허가", "FDA")),
            ("LITIGATION", ("소송", "판결")),
            ("ACCIDENT", ("사고", "화재", "리콜")),
            ("CAPITAL_ACTION", ("유상증자", "전환사채", "자사주", "배당")),
            ("MANAGEMENT", ("대표이사", "최대주주", "경영권")),
            ("PRODUCT", ("신제품", "출시")),
            ("SUPPLY_CHAIN", ("공급망", "납품", "고객사")),
        ]
        return next((name for name, words in rules if any(word.lower() in text.lower() for word in words)), "OTHER")

    @classmethod
    def extract(cls, item: RawNewsItem) -> list[dict[str, Any]]:
        text = item.body or item.summary or item.title
        facts: list[dict[str, Any]] = []
        for match in cls.amount_pattern.finditer(text):
            try:
                number = float(match.group("num").replace(",", ""))
            except ValueError:
                continue
            multiplier = {"조": 1_000_000_000_000, "억": 100_000_000, "만": 10_000, None: 1}[match.group("unit")]
            sentence = next((part.strip() for part in re.split(r"[.!?\n]", text) if match.group(0) in part), text[:240])
            facts.append({
                "fact_type": cls.event_type(text),
                "subject": item.company_names_explicit[0] if item.company_names_explicit else "",
                "object": None,
                "amount": round(number * multiplier),
                "currency": "KRW",
                "percentage": None,
                "quantity": None,
                "date": None,
                "period": None,
                "location": None,
                "source_sentence": sentence[:500],
                "confidence": 0.9 if item.body else 0.65 if item.summary else 0.35,
            })
        if not facts:
            facts.append({
                "fact_type": cls.event_type(text), "subject": item.company_names_explicit[0] if item.company_names_explicit else "",
                "object": None, "amount": None, "currency": None, "percentage": None, "quantity": None,
                "date": None, "period": None, "location": None, "source_sentence": text[:500],
                "confidence": 0.75 if item.body else 0.5 if item.summary else 0.25,
            })
        return facts


class NewsDeduplicationEngine:
    def __init__(self, resolver: CompanyEntityResolver):
        self.resolver = resolver

    @staticmethod
    def _similarity(left: RawNewsItem, right: RawNewsItem) -> tuple[float, float]:
        title = jaccard(normalized_tokens(left.title), normalized_tokens(right.title))
        lbody = left.body or left.summary or ""
        rbody = right.body or right.summary or ""
        body = SequenceMatcher(None, clean_text(lbody).lower(), clean_text(rbody).lower()).ratio() if lbody and rbody else 0.0
        return title, body

    def same_event(self, left: dict[str, Any], item: RawNewsItem, links: list[dict[str, Any]], facts: list[dict[str, Any]]) -> bool:
        if item.article_id in left["article_ids"] or canonicalize_url(item.url) in left["urls"]:
            return True
        if not set(left["symbols"]) & {link["symbol"] for link in links}:
            return False
        if abs((parse_time(item.published_at) - parse_time(left["first_published_at"])).total_seconds()) > 72 * 3600:
            return False
        comparisons = [
            self._similarity(existing, item)
            for existing in left.get("_articles", [left["_representative"]])
        ]
        title_sim = max((pair[0] for pair in comparisons), default=0.0)
        body_sim = max((pair[1] for pair in comparisons), default=0.0)
        left_type = left["event_type"]
        right_type = facts[0]["fact_type"] if facts else "OTHER"
        if left_type != "OTHER" and right_type != "OTHER" and left_type != right_type:
            return False
        left_amounts = {fact["amount"] for fact in left["facts"] if fact.get("amount")}
        right_amounts = {fact["amount"] for fact in facts if fact.get("amount")}
        amounts_match = bool(left_amounts and right_amounts and any(
            abs(a - b) / max(abs(a), abs(b), 1) <= 0.02 for a in left_amounts for b in right_amounts
        ))
        amounts_conflict = bool(left_amounts and right_amounts and not any(
            abs(a - b) / max(abs(a), abs(b), 1) <= 0.02 for a in left_amounts for b in right_amounts
        ))
        if amounts_conflict and title_sim < 0.72 and body_sim < 0.8:
            return False
        if left_type == right_type and left_type != "OTHER" and amounts_match and title_sim >= 0.25:
            return True
        return title_sim >= 0.48 or body_sim >= 0.76

    @staticmethod
    def source_family(item: RawNewsItem, cluster: dict[str, Any]) -> str:
        if item.is_disclosure or item.publisher_domain in PRIMARY_DOMAINS:
            receipt = item.metadata.get("receipt_no")
            return f"primary:{receipt or item.publisher_domain}"
        if item.original_publisher:
            return f"origin:{item.original_publisher.lower()}"
        content = clean_text(item.body or item.summary or item.title)
        fingerprint = text_fingerprint(content)
        for existing in cluster.get("_articles", []):
            other = clean_text(existing.body or existing.summary or existing.title)
            if SequenceMatcher(None, content.lower(), other.lower()).ratio() >= 0.9:
                return f"copy:{text_fingerprint(other)}"
        return f"domain:{item.publisher_domain or item.publisher}"

    def cluster(self, items: list[RawNewsItem]) -> list[dict[str, Any]]:
        clusters: list[dict[str, Any]] = []
        seen_article_ids: set[str] = set()
        seen_urls: set[str] = set()
        for item in sorted(items, key=lambda row: row.published_at):
            normalized_url = canonicalize_url(item.url)
            if item.article_id in seen_article_ids or (normalized_url and normalized_url in seen_urls):
                continue
            seen_article_ids.add(item.article_id)
            if normalized_url:
                seen_urls.add(normalized_url)
            links = self.resolver.resolve(item)
            if not links:
                continue
            facts = FactExtractor.extract(item)
            target = next((cluster for cluster in clusters if self.same_event(cluster, item, links, facts)), None)
            if target is None:
                seed = f"{links[0]['symbol']}|{FactExtractor.event_type(item.title)}|{text_fingerprint(item.title)}"
                target = {
                    "cluster_id": hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24],
                    "canonical_title": item.title,
                    "canonical_summary": item.summary or item.body or "",
                    "event_type": facts[0]["fact_type"],
                    "first_published_at": item.published_at,
                    "last_updated_at": item.published_at,
                    "article_ids": [], "urls": [], "symbols": [], "entities": [],
                    "facts": [], "contradictions": [], "_articles": [],
                    "_representative": item, "_source_families": set(),
                }
                clusters.append(target)
            target["article_ids"].append(item.article_id)
            target["urls"].append(canonicalize_url(item.url))
            target["_articles"].append(item)
            target["symbols"] = list(dict.fromkeys(target["symbols"] + [link["symbol"] for link in links]))
            target["entities"].extend(link for link in links if link not in target["entities"])
            target["facts"].extend(facts)
            target["last_updated_at"] = max(target["last_updated_at"], item.published_at)
            target["_source_families"].add(self.source_family(item, target))

        output: list[dict[str, Any]] = []
        for cluster in clusters:
            articles: list[RawNewsItem] = cluster["_articles"]
            amounts = sorted({fact["amount"] for fact in cluster["facts"] if fact.get("amount")})
            if len(amounts) > 1 and amounts[-1] / max(amounts[0], 1) > 1.02:
                cluster["contradictions"].append({
                    "field": "amount", "values": amounts,
                    "message": "동일 사건으로 묶인 출처 간 금액이 일치하지 않습니다.",
                })
            primary_articles = [
                article for article in articles
                if article.is_disclosure or article.publisher_domain in PRIMARY_DOMAINS
            ]
            primary_families = {
                f"primary:{article.metadata.get('receipt_no') or article.publisher_domain}"
                for article in primary_articles
            }
            independent_articles = [article for article in articles if article not in primary_articles]
            parents = list(range(len(independent_articles)))

            def find(index: int) -> int:
                while parents[index] != index:
                    parents[index] = parents[parents[index]]
                    index = parents[index]
                return index

            def union(left: int, right: int) -> None:
                lroot, rroot = find(left), find(right)
                if lroot != rroot:
                    parents[rroot] = lroot

            def agency_origin(article: RawNewsItem) -> str | None:
                candidates = [
                    article.original_publisher,
                    article.publisher,
                    str(article.metadata.get("original_publisher") or ""),
                    str(article.metadata.get("source") or ""),
                ]
                for candidate in candidates:
                    lowered = clean_text(candidate).lower()
                    for agency in AGENCY_NAMES:
                        if agency.lower() in lowered:
                            return agency.lower()
                return None

            for left_index, left in enumerate(independent_articles):
                for right_index in range(left_index):
                    right = independent_articles[right_index]
                    left_agency, right_agency = agency_origin(left), agency_origin(right)
                    same_declared_origin = bool(
                        left.original_publisher and right.original_publisher
                        and left.original_publisher.lower() == right.original_publisher.lower()
                    )
                    same_wire_agency = bool(left_agency and right_agency and left_agency == right_agency)
                    same_publisher = bool(
                        (left.publisher_domain or left.publisher)
                        and (left.publisher_domain or left.publisher) == (right.publisher_domain or right.publisher)
                    )
                    left_text = clean_text(left.body or left.summary or left.title)
                    right_text = clean_text(right.body or right.summary or right.title)
                    same_copy = bool(
                        left_text and right_text
                        and SequenceMatcher(None, left_text.lower(), right_text.lower()).ratio() >= 0.9
                    )
                    title_similarity = jaccard(normalized_tokens(left.title), normalized_tokens(right.title))
                    press_release_copy = bool(
                        (left.is_press_release or right.is_press_release)
                        and left_text and right_text
                        and (
                            SequenceMatcher(None, left_text.lower(), right_text.lower()).ratio() >= 0.72
                            or title_similarity >= 0.72
                        )
                    )
                    if same_declared_origin or same_wire_agency or same_publisher or same_copy or press_release_copy:
                        union(left_index, right_index)
            independent_roots = [find(index) for index in range(len(independent_articles))]
            independent_source_count = len(set(independent_roots))
            family_sizes: dict[int, int] = {}
            for root in independent_roots:
                family_sizes[root] = family_sizes.get(root, 0) + 1
            syndicated = (
                sum(max(count - 1, 0) for count in family_sizes.values())
                + max(len(primary_articles) - len(primary_families), 0)
            )
            payload = {key: value for key, value in cluster.items() if not key.startswith("_") and key != "urls"}
            payload.update({
                "raw_article_count": len(articles),
                "publisher_count": len({article.publisher_domain or article.publisher for article in articles}),
                "independent_source_count": independent_source_count,
                "primary_source_count": len(primary_families),
                "syndicated_copy_count": syndicated,
                "verification_count_basis": "INDEPENDENT_ORIGINAL_SOURCES_AND_PRIMARY_SOURCES",
                "dedup_confidence": round(min(0.99, 0.65 + 0.07 * max(len(articles) - 1, 0)), 2),
                "articles": [asdict(article) for article in articles],
            })
            output.append(payload)
        return sorted(output, key=lambda row: row["first_published_at"], reverse=True)


class NewsVerificationEngine:
    @staticmethod
    def verify(cluster: dict[str, Any]) -> dict[str, Any]:
        articles = cluster.get("articles", [])
        body_count = sum(bool(item.get("body")) for item in articles)
        summary_count = sum(bool(item.get("summary")) for item in articles)
        if any(item.get("is_retraction") for item in articles):
            status, reason = "RETRACTED", "철회 또는 취소 원문이 확인됨"
        elif cluster.get("contradictions"):
            status, reason = "CONFLICTED", "독립 출처 간 핵심 수치 또는 사실이 충돌함"
        elif cluster.get("primary_source_count", 0) >= 1:
            status, reason = "VERIFIED_PRIMARY", "DART·거래소·기업 공식 원출처가 존재함"
        elif cluster.get("independent_source_count", 0) >= 2 and body_count >= 2:
            status, reason = "VERIFIED_MULTI_SOURCE", "재배포가 아닌 독립 본문 출처 2곳 이상 확인"
        elif cluster.get("independent_source_count", 0) >= 2 and summary_count >= 2:
            status, reason = "PARTIALLY_VERIFIED", "독립 요약 출처는 2곳 이상이나 기사 본문 미수집"
        elif cluster.get("independent_source_count", 0) == 1 and (body_count or summary_count):
            status, reason = "SINGLE_SOURCE", "독립 출처가 1곳뿐임"
        else:
            status, reason = "UNVERIFIED", "원출처 또는 기사 본문을 확인할 수 없음"
        return {
            "status": status,
            "reason": reason,
            "article_count": cluster.get("raw_article_count", 0),
            "independent_source_count": cluster.get("independent_source_count", 0),
            "primary_source_count": cluster.get("primary_source_count", 0),
            "syndicated_copy_count": cluster.get("syndicated_copy_count", 0),
            "body_collected_count": body_count,
            "decided_by": "BACKEND_RULES",
        }


class NewsMaterialityScorer:
    @staticmethod
    def score(cluster: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
        status = verification["status"]
        score = {
            "VERIFIED_PRIMARY": 34, "VERIFIED_MULTI_SOURCE": 30,
            "PARTIALLY_VERIFIED": 18, "SINGLE_SOURCE": 8,
            "CONFLICTED": 5, "UNVERIFIED": 0, "RETRACTED": 0,
        }[status]
        event_weights = {
            "CONTRACT": 26, "APPROVAL": 26, "EARNINGS": 23, "REGULATION": 22,
            "ACCIDENT": 24, "CAPITAL_ACTION": 24, "LITIGATION": 18,
            "INVESTMENT": 18, "MANAGEMENT": 18, "PRODUCT": 13,
            "SUPPLY_CHAIN": 16, "OTHER": 7,
        }
        score += event_weights.get(cluster.get("event_type", "OTHER"), 7)
        score += min(cluster.get("independent_source_count", 0), 3) * 5
        amounts = [fact["amount"] for fact in cluster.get("facts", []) if fact.get("amount")]
        if amounts:
            score += 6
        limitations = []
        if not amounts:
            limitations.append("회사 규모 대비 금액 비율 데이터 없음")
        if verification.get("body_collected_count", 0) == 0 and status != "VERIFIED_PRIMARY":
            score = min(score, 49)
            limitations.append("기사 본문 미수집")
        if status in {"CONFLICTED", "UNVERIFIED", "RETRACTED"}:
            score = min(score, 29)
        score = max(0, min(100, round(score)))
        label = "매우 중요" if score >= 85 else "중요" if score >= 70 else "보통" if score >= 50 else "낮음" if score >= 30 else "잡음 가능성"
        return {"score": score, "label": label, "limitations": limitations, "basis": "RULE_BASED"}


class CandidateScoringEngine:
    @staticmethod
    def sentiment(cluster: dict[str, Any]) -> dict[str, Any]:
        text = f"{cluster.get('canonical_title', '')} {cluster.get('canonical_summary', '')}"
        positive = sum(word.lower() in text.lower() for word in POSITIVE_WORDS)
        negative = sum(word.lower() in text.lower() for word in NEGATIVE_WORDS)
        if positive >= negative + 2:
            label = "STRONG_POSITIVE"
        elif positive > negative:
            label = "POSITIVE"
        elif negative >= positive + 2:
            label = "STRONG_NEGATIVE"
        elif negative > positive:
            label = "NEGATIVE"
        elif positive and negative:
            label = "MIXED"
        else:
            label = "NEUTRAL"
        return {
            "sentiment": label,
            "confidence": round(min(0.9, 0.45 + 0.1 * abs(positive - negative)), 2),
            "positive_keyword_count": positive,
            "negative_keyword_count": negative,
            "basis": "RULE_BASED_TEXT",
        }

    @staticmethod
    def score(
        cluster: dict[str, Any],
        verification: dict[str, Any],
        materiality: dict[str, Any],
        sentiment: dict[str, Any],
        reaction: dict[str, Any],
        flow: dict[str, Any],
        data_quality: int,
        settings: Settings,
    ) -> dict[str, Any]:
        verification_points = {
            "VERIFIED_PRIMARY": 20, "VERIFIED_MULTI_SOURCE": 18,
            "PARTIALLY_VERIFIED": 10, "SINGLE_SOURCE": 4,
            "CONFLICTED": 0, "UNVERIFIED": 0, "RETRACTED": 0,
        }[verification["status"]]
        sentiment_points = {
            "STRONG_POSITIVE": 10, "POSITIVE": 8, "MIXED": 3, "NEUTRAL": 2,
            "NEGATIVE": 0, "STRONG_NEGATIVE": 0,
        }[sentiment["sentiment"]]
        price_ok = reaction.get("price_confirmation") is True
        volume_ok = reaction.get("volume_confirmation") is True
        institution_ok = flow.get("institution_confirmation") is True
        foreign_ok = flow.get("foreign_confirmation") is True
        member_ok = flow.get("member_confirmation") is True
        flow_ok = institution_ok or foreign_ok or member_ok
        groups = [name for name, ok in (("가격", price_ok), ("거래량", volume_ok), ("수급", flow_ok)) if ok]
        risk_penalty = 0
        risks: list[str] = []
        return_5m = reaction.get("return_after_news_5m")
        if isinstance(return_5m, (int, float)) and return_5m > 7:
            risk_penalty -= 12
            risks.append("뉴스 이후 단기 과도한 급등")
        if flow.get("institution_confirmation") is False and flow.get("foreign_confirmation") is False:
            risk_penalty -= 12
            risks.append("외국인·기관 동시 순매도")
        if verification["status"] in {"CONFLICTED", "RETRACTED"}:
            risk_penalty -= 30
            risks.append("정정·철회 또는 핵심 사실 충돌")
        market_risks = flow.get("market_risk_flags") or []
        if market_risks:
            risk_penalty -= 40
            risks.append("거래정지·투자경고·과열 등 시장 위험 상태")
        if data_quality < settings.news_min_data_quality:
            risk_penalty -= 10
            risks.append("뉴스·반응 데이터 품질 부족")
        breakdown = {
            "verification": verification_points,
            "materiality": round(materiality["score"] * 0.15),
            "sentiment": sentiment_points,
            "price_confirmation": 15 if price_ok else 0,
            "volume_confirmation": 10 if volume_ok else 0,
            "institution_confirmation": 10 if institution_ok else 0,
            "foreign_confirmation": 5 if foreign_ok else 0,
            "member_confirmation": 5 if member_ok else 0,
            "sector_confirmation": 0,
            "data_quality": round(data_quality * 0.05),
            "risk_penalty": risk_penalty,
        }
        score = max(0, min(100, sum(breakdown.values())))
        age = (utcnow() - parse_time(cluster["first_published_at"])).total_seconds()
        verified = verification["status"] in {"VERIFIED_PRIMARY", "VERIFIED_MULTI_SOURCE"}
        positive = sentiment["sentiment"] in {"POSITIVE", "STRONG_POSITIVE"}
        fresh = 0 <= age <= 6 * 3600
        eligible = (
            verified and positive and fresh and materiality["score"] >= settings.news_min_materiality
            and len(groups) >= 2 and data_quality >= settings.news_min_data_quality
            and risk_penalty > -20 and not market_risks
        )
        if eligible:
            action = "STRONG_BUY_REVIEW" if score >= 80 else "BUY_REVIEW"
        elif verified and positive:
            action = "WATCH" if score >= 50 else "WAIT"
        elif sentiment["sentiment"] in {"NEGATIVE", "STRONG_NEGATIVE"} and verified:
            action = "SELL_REVIEW" if price_ok is False and flow_ok is False else "WATCH"
        else:
            action = "NO_TRADE"
        blocking = []
        if not verified:
            blocking.append("1차 원출처 또는 독립 본문 출처 2곳 미확인")
        if not positive:
            blocking.append("긍정 방향 뉴스가 아님")
        if len(groups) < 2:
            blocking.append("가격·거래량·수급 중 독립 확인 그룹 2개 미만")
        if not fresh:
            blocking.append("뉴스 만료 또는 미래 시각 오류")
        if materiality["score"] < settings.news_min_materiality:
            blocking.append("사용자 중요도 기준 미달")
        if market_risks:
            blocking.append(f"시장 위험 상태: {', '.join(market_risks)}")
        return {
            "action": action,
            "candidate_score": score,
            "score_label": "현재 뉴스·수급·가격 확인 점수",
            "score_breakdown": breakdown,
            "confirmation_groups": groups,
            "confirmations": [
                text for text, ok in (
                    ("가격 반응 확인", price_ok), ("거래량 반응 확인", volume_ok),
                    ("기관 투자자 순매수 확인", institution_ok), ("외국인 순매수 확인", foreign_ok),
                    ("회원사 창구 매수 집중 확인", member_ok),
                ) if ok
            ],
            "risks": risks,
            "blocking_reasons": blocking,
            "eligible": eligible,
            "statistics_available": False,
            "automatic_order": False,
        }


class NewsPipelineService:
    feature_specs: dict[str, tuple[str, str, str]] = {
        "news_verification_status": ("뉴스 사실 검증", "", "neutral"),
        "news_event_count_5m": ("사건 클러스터 5분", "건", "neutral"),
        "news_event_count_15m": ("사건 클러스터 15분", "건", "neutral"),
        "news_event_count_60m": ("사건 클러스터 60분", "건", "neutral"),
        "unique_news_event_count": ("독립 사건 클러스터", "건", "neutral"),
        "raw_article_count": ("원시 기사 수", "건", "neutral"),
        "independent_source_count": ("독립 원출처 수", "곳", "positive"),
        "primary_source_count": ("1차 공식 출처 수", "곳", "positive"),
        "syndicated_copy_count": ("재배포 기사 수", "건", "warning"),
        "source_diversity": ("출처 다양성", "곳", "positive"),
        "news_burst_zscore": ("사건 발생 급증", "z", "warning"),
        "verified_news_count": ("검증 사건 수", "건", "positive"),
        "conflicted_news_count": ("충돌 사건 수", "건", "negative"),
        "retracted_news_count": ("철회 사건 수", "건", "negative"),
        "positive_news_score": ("긍정 뉴스 점수", "점", "positive"),
        "negative_news_score": ("부정 뉴스 점수", "점", "negative"),
        "net_news_score": ("순 뉴스 방향 점수", "점", "neutral"),
        "news_materiality_max": ("최대 뉴스 중요도", "점", "neutral"),
        "news_materiality_mean": ("평균 뉴스 중요도", "점", "neutral"),
        "latest_news_age_seconds": ("최신 뉴스 경과", "초", "neutral"),
        "news_price_confirmation": ("뉴스 이후 가격 확인", "", "neutral"),
        "news_volume_confirmation": ("뉴스 이후 거래량 확인", "", "neutral"),
        "news_institution_confirmation": ("기관 투자자 확인", "", "neutral"),
        "news_foreign_confirmation": ("외국인 확인", "", "neutral"),
        "news_member_flow_confirmation": ("회원사 창구 확인", "", "neutral"),
        "news_sector_confirmation": ("업종 확인", "", "neutral"),
        "news_already_priced_score": ("선반영 위험", "점", "negative"),
        "news_contradiction_score": ("뉴스 충돌 점수", "점", "negative"),
        "news_data_quality_score": ("뉴스 데이터 품질", "점", "positive"),
        "dart_rights_issue": ("DART 유상증자 사건", "건", "negative"),
        "dart_capital_reduction": ("DART 감자 사건", "건", "negative"),
        "dart_cb_bw": ("DART CB/BW 사건", "건", "warning"),
        "dart_major_holder": ("DART 최대주주 변경 사건", "건", "warning"),
        "dart_embezzlement": ("DART 횡령·배임 사건", "건", "negative"),
        "dart_default": ("DART 부도·회생 사건", "건", "negative"),
        "dart_audit": ("DART 감사의견 사건", "건", "warning"),
        "dart_lawsuit": ("DART 소송 사건", "건", "warning"),
        "institution_net_buy_after_news_1m": ("뉴스 후 기관 순매수 1분", "원", "positive"),
        "institution_net_buy_after_news_3m": ("뉴스 후 기관 순매수 3분", "원", "positive"),
        "institution_net_buy_after_news_5m": ("뉴스 후 기관 순매수 5분", "원", "positive"),
        "institution_net_buy_after_news_15m": ("뉴스 후 기관 순매수 15분", "원", "positive"),
        "institution_flow_acceleration_after_news": ("뉴스 후 기관 수급 가속", "원", "positive"),
        "foreign_net_buy_after_news_1m": ("뉴스 후 외국인 순매수 1분", "원", "positive"),
        "foreign_net_buy_after_news_5m": ("뉴스 후 외국인 순매수 5분", "원", "positive"),
        "foreign_flow_acceleration_after_news": ("뉴스 후 외국인 수급 가속", "원", "positive"),
        "foreign_institution_joint_buy_after_news": ("뉴스 후 외국인·기관 동시매수", "", "positive"),
        "foreign_institution_joint_sell_after_news": ("뉴스 후 외국인·기관 동시매도", "", "negative"),
        "institution_price_divergence_after_news": ("뉴스 후 기관-가격 괴리", "", "warning"),
        "institution_volume_share_after_news": ("뉴스 후 기관 거래비중", "%", "positive"),
        "member_buy_concentration_after_news": ("뉴스 후 회원사 매수 집중", "%", "positive"),
        "member_sell_concentration_after_news": ("뉴스 후 회원사 매도 집중", "%", "negative"),
        "foreign_member_net_buy_after_news": ("뉴스 후 외국계 회원사 순매수", "주", "positive"),
        "member_flow_reversal_after_news": ("뉴스 후 회원사 흐름 반전", "", "warning"),
        "program_net_buy_after_news": ("뉴스 후 프로그램 순매수", "원", "positive"),
        "pension_flow_after_news": ("뉴스 후 연기금 수급", "원", "positive"),
        "investment_trust_flow_after_news": ("뉴스 후 투신 수급", "원", "positive"),
        "financial_investment_flow_after_news": ("뉴스 후 금융투자 수급", "원", "positive"),
        "private_equity_flow_after_news": ("뉴스 후 사모펀드 수급", "원", "positive"),
        "return_after_news_1m": ("뉴스 후 수익률 1분", "%", "positive"),
        "return_after_news_3m": ("뉴스 후 수익률 3분", "%", "positive"),
        "return_after_news_5m": ("뉴스 후 수익률 5분", "%", "positive"),
        "return_after_news_15m": ("뉴스 후 수익률 15분", "%", "positive"),
        "volume_ratio_after_news": ("뉴스 후 거래량 비율", "배", "positive"),
        "turnover_after_news": ("뉴스 후 거래대금", "원", "positive"),
        "vwap_distance_after_news": ("뉴스 후 VWAP 이격", "%", "positive"),
        "high_breakout_after_news": ("뉴스 후 고점 돌파", "", "positive"),
        "low_breakdown_after_news": ("뉴스 후 저점 이탈", "", "negative"),
        "spread_change_after_news": ("뉴스 후 스프레드 변화", "", "warning"),
        "orderbook_imbalance_after_news": ("뉴스 후 호가 불균형", "", "neutral"),
        "upper_wick_after_news": ("뉴스 후 윗꼬리", "%", "negative"),
        "price_reversal_after_news": ("뉴스 후 가격 반전", "", "warning"),
        "sector_return_after_news": ("뉴스 후 업종 수익률", "%", "positive"),
        "sector_breadth_after_news": ("뉴스 후 업종 breadth", "%", "positive"),
        "peer_confirmation_after_news": ("뉴스 후 동종주 확인", "", "positive"),
        "competitor_divergence_after_news": ("뉴스 후 경쟁사 괴리", "", "warning"),
        "market_adjusted_return_after_news": ("뉴스 후 시장조정 수익률", "%", "positive"),
    }

    institution_feature_ids = [
        "institution_net", "institution_net_buy_after_news_1m",
        "institution_net_buy_after_news_3m", "institution_net_buy_after_news_5m",
        "institution_net_buy_after_news_15m", "institution_flow_acceleration_after_news",
        "foreign_net", "foreign_net_buy_after_news_1m", "foreign_net_buy_after_news_5m",
        "foreign_institution_joint_buy_after_news", "member_buy_concentration_after_news",
        "member_sell_concentration_after_news",
    ]

    def __init__(self, settings: Settings, db: Database, root: Path, asset_root: Path | None = None):
        self.settings = settings
        self.db = db
        self.root = root
        self.asset_root = asset_root or root
        self.resolver = CompanyEntityResolver()
        self.deduper = NewsDeduplicationEngine(self.resolver)
        self.providers: list[NewsProvider] = []
        self.articles: list[RawNewsItem] = []
        self.clusters: list[dict[str, Any]] = []
        self.overviews: dict[str, dict[str, Any]] = {}
        self.provider_statuses: list[dict[str, Any]] = []
        self.last_refresh: datetime | None = None
        self._lock = asyncio.Lock()
        self.configure_providers()

    def configure_providers(self) -> None:
        external_manual = self.root / "data" / "news_manual.json"
        manual_path = external_manual if external_manual.exists() else self.asset_root / "data" / "news_manual.json"
        naver_verified = provider_secret_verified("naver_news", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")
        dart_verified = provider_secret_verified("open_dart", "OPEN_DART_API_KEY")
        self.providers = [
            NaverFinanceNewsProvider(),
            DartRssNewsProvider(),
            NaverNewsProvider(
                self.settings.naver_client_id if naver_verified else "",
                self.settings.naver_client_secret if naver_verified else "",
            ),
            OpenDartNewsProvider(self.settings.open_dart_api_key if dart_verified else ""),
            JsonNewsProvider("manual_news_json", manual_path, True, "MANUAL"),
            JsonNewsProvider(
                "fixture_news", self.asset_root / "examples" / "news_fixture.json",
                self.settings.news_fixture_mode and self.settings.data_mode == "demo", "SIMULATED",
            ),
            UnavailableNewsProvider("krx_disclosure", "KRX 공시 어댑터 미연결"),
            UnavailableNewsProvider("company_ir", "기업별 IR 어댑터 미연결"),
            UnavailableNewsProvider("gdelt_news", "GDELT 뉴스 어댑터 미연결"),
        ]

    async def refresh(self, symbols: list[str], force: bool = False) -> dict[str, Any]:
        async with self._lock:
            if (
                not force and self.last_refresh
                and (utcnow() - self.last_refresh).total_seconds() < self.settings.news_refresh_seconds
            ):
                return {"refreshed": False, "article_count": len(self.articles), "cluster_count": len(self.clusters)}
            since = utcnow() - timedelta(hours=24)
            statuses = await asyncio.gather(*(provider.health_check() for provider in self.providers))
            self.provider_statuses = [asdict(status) for status in statuses]
            active_providers = [
                provider for provider, status in zip(self.providers, statuses) if status.available
            ]
            tasks = [provider.fetch_latest(symbols, since, 120) for provider in active_providers]
            batches = await asyncio.gather(*tasks, return_exceptions=True)
            articles: list[RawNewsItem] = []
            for provider, batch in zip(active_providers, batches):
                if isinstance(batch, Exception):
                    for status in self.provider_statuses:
                        if status["provider_id"] == provider.provider_id:
                            status.update({
                                "available": False,
                                "state": "ERROR",
                                "message": f"수집 실패: {type(batch).__name__}",
                                "checked_at": utcnow().isoformat(),
                            })
                    continue
                articles.extend(batch)
            self.articles = list({item.article_id: item for item in articles}.values())
            self.clusters = self.deduper.cluster(self.articles)
            for cluster in self.clusters:
                verification = NewsVerificationEngine.verify(cluster)
                cluster["verification"] = verification
                cluster["materiality"] = NewsMaterialityScorer.score(cluster, verification)
                cluster["sentiment"] = CandidateScoringEngine.sentiment(cluster)
            self.last_refresh = utcnow()
            try:
                self.db.save_news_pipeline_state(self.articles, self.clusters, [], self.provider_statuses)
            except Exception as exc:
                logger.warning("뉴스 상태 DB 저장 실패(시세 기능은 계속): %s", type(exc).__name__)
            return {"refreshed": True, "article_count": len(self.articles), "cluster_count": len(self.clusters)}

    @staticmethod
    def _reaction(frame: pd.DataFrame, event_time: str) -> dict[str, Any]:
        unavailable = {
            "return_after_news_1m": None, "return_after_news_3m": None,
            "return_after_news_5m": None, "return_after_news_15m": None,
            "volume_ratio_after_news": None, "turnover_after_news": None,
            "vwap_distance_after_news": None, "high_breakout_after_news": None,
            "low_breakdown_after_news": None, "upper_wick_after_news": None,
            "price_confirmation": None, "volume_confirmation": None,
            "basis_time": event_time, "future_data_used": False,
        }
        if frame is None or frame.empty:
            return unavailable
        work = frame.copy()
        work["_time"] = pd.to_datetime(work["time"], utc=True, errors="coerce")
        work = work.dropna(subset=["_time"]).sort_values("_time")
        event = pd.Timestamp(parse_time(event_time))
        before = work[work["_time"] < event]
        after = work[work["_time"] >= event]
        if before.empty or after.empty:
            return unavailable
        baseline = float(before.iloc[-1]["close"])
        latest = float(after.iloc[-1]["close"])
        result = dict(unavailable)
        elapsed = (after.iloc[-1]["_time"] - event).total_seconds() / 60
        for minutes in (1, 3, 5, 15):
            horizon = pd.Timestamp(parse_time(event_time) + timedelta(minutes=minutes))
            eligible = after[after["_time"] >= horizon]
            result[f"return_after_news_{minutes}m"] = (
                round((float(eligible.iloc[0]["close"]) / baseline - 1) * 100, 4)
                if not eligible.empty and elapsed >= minutes else None
            )
        pre_volume = float(before.tail(20)["volume"].mean()) if len(before) else 0
        post_volume = float(after["volume"].mean()) if len(after) else 0
        result["volume_ratio_after_news"] = round(post_volume / pre_volume, 3) if pre_volume > 0 else None
        result["turnover_after_news"] = round(float((after["close"] * after["volume"]).sum()))
        total_volume = float(after["volume"].sum())
        vwap = float((after["close"] * after["volume"]).sum() / total_volume) if total_volume > 0 else None
        result["vwap_distance_after_news"] = round((latest / vwap - 1) * 100, 4) if vwap else None
        result["high_breakout_after_news"] = latest > float(before.tail(20)["high"].max())
        result["low_breakdown_after_news"] = latest < float(before.tail(20)["low"].min())
        last = after.iloc[-1]
        candle_range = float(last["high"]) - float(last["low"])
        result["upper_wick_after_news"] = round(
            (float(last["high"]) - max(float(last["open"]), float(last["close"]))) / candle_range * 100, 2
        ) if candle_range > 0 else 0.0
        result["price_confirmation"] = bool(
            latest > baseline and (
                (result["vwap_distance_after_news"] or 0) > 0 or result["high_breakout_after_news"]
            )
        )
        result["volume_confirmation"] = bool((result["volume_ratio_after_news"] or 0) >= 1.3)
        return result

    @staticmethod
    def _flow(flat: dict[str, Any], members: list[dict[str, Any]], event_time: str, observed_at: str) -> dict[str, Any]:
        observation_after = parse_time(observed_at) >= parse_time(event_time)
        institution = flat.get("institution_net")
        foreign = flat.get("foreign_net")
        total_buy = sum(max(float(row.get("buy") or 0), 0) for row in members)
        total_sell = sum(max(float(row.get("sell") or 0), 0) for row in members)
        top_buy = max((float(row.get("buy") or 0) for row in members), default=0)
        top_sell = max((float(row.get("sell") or 0) for row in members), default=0)
        member_buy_concentration = top_buy / total_buy * 100 if total_buy > 0 else None
        member_sell_concentration = top_sell / total_sell * 100 if total_sell > 0 else None
        member_confirmation = (
            member_buy_concentration is not None and member_sell_concentration is not None
            and member_buy_concentration > member_sell_concentration
        ) if observation_after else None
        risk_labels = {
            "trading_halt": "거래정지",
            "investment_warning": "투자경고",
            "overheated": "단기과열",
            "management_issue": "관리종목",
        }
        market_risk_flags = [
            label for key, label in risk_labels.items()
            if flat.get(key) is True or (
                isinstance(flat.get(key), (int, float)) and flat.get(key) > 0
            )
        ]
        return {
            "institution_current_net": institution if observation_after else None,
            "foreign_current_net": foreign if observation_after else None,
            "institution_confirmation": bool(institution > 0) if isinstance(institution, (int, float)) and observation_after else None,
            "foreign_confirmation": bool(foreign > 0) if isinstance(foreign, (int, float)) and observation_after else None,
            "member_confirmation": member_confirmation,
            "member_buy_concentration_after_news": round(member_buy_concentration, 2) if member_buy_concentration is not None else None,
            "member_sell_concentration_after_news": round(member_sell_concentration, 2) if member_sell_concentration is not None else None,
            "institution_after_news_delta": None,
            "foreign_after_news_delta": None,
            "institution_note": "기관 투자자 장중 누적값이며 뉴스 이후 증분 시계열은 미제공",
            "member_note": "증권사 회원사 창구 흐름이며 기관 투자자 분류가 아님",
            "market_risk_flags": market_risk_flags,
            "observed_at": observed_at,
        }

    def evaluate(
        self,
        symbol: str,
        frame: pd.DataFrame,
        flat: dict[str, Any],
        members: list[dict[str, Any]],
        mode: str,
        observed_at: str,
    ) -> dict[str, Any]:
        clusters = [cluster for cluster in self.clusters if symbol in cluster.get("symbols", [])]
        candidates: list[dict[str, Any]] = []
        evaluated: list[dict[str, Any]] = []
        for cluster in clusters:
            reaction = self._reaction(frame, cluster["first_published_at"])
            flow = self._flow(flat, members, cluster["first_published_at"], observed_at)
            verification = cluster["verification"]
            materiality = cluster["materiality"]
            sentiment = cluster["sentiment"]
            body_ratio = verification["body_collected_count"] / max(verification["article_count"], 1)
            data_quality = round(min(100,
                (35 if verification["status"] in {"VERIFIED_PRIMARY", "VERIFIED_MULTI_SOURCE"} else 15)
                + body_ratio * 25
                + (20 if reaction["price_confirmation"] is not None else 0)
                + (20 if any(flow.get(key) is not None for key in ("institution_confirmation", "foreign_confirmation", "member_confirmation")) else 0)
            ))
            scored = CandidateScoringEngine.score(
                cluster, verification, materiality, sentiment, reaction, flow,
                data_quality, self.settings,
            )
            entry = {
                "cluster_id": cluster["cluster_id"],
                "symbol": symbol,
                "name": STOCK_BY_SYMBOL.get(symbol, {}).get("name", symbol),
                "news_title": cluster["canonical_title"],
                "news_time": cluster["first_published_at"],
                "news_age_minutes": max(0, round((utcnow() - parse_time(cluster["first_published_at"])).total_seconds() / 60, 1)),
                "verification": verification,
                "materiality": materiality,
                "sentiment": sentiment,
                "price_volume_reaction": reaction,
                "flow_confirmation": flow,
                "data_quality": data_quality,
                "statistics_available": False,
                "automatic_order": False,
                **scored,
            }
            evaluated.append({**cluster, "evaluation": entry})
            if entry["action"] in {"STRONG_BUY_REVIEW", "BUY_REVIEW", "WATCH", "WAIT", "SELL_REVIEW"}:
                candidates.append(entry)

        latest = evaluated[0] if evaluated else None
        feature_values = self._feature_values(symbol, evaluated, latest, mode)
        promoted = []
        if latest and latest["materiality"]["score"] >= self.settings.news_min_materiality:
            promoted = list(dict.fromkeys(
                self.institution_feature_ids + [
                    "news_verification_status", "news_materiality_max",
                    "independent_source_count", "primary_source_count",
                    "news_price_confirmation", "news_volume_confirmation",
                    "return_after_news_1m", "return_after_news_3m",
                    "return_after_news_5m", "volume_ratio_after_news",
                    "vwap_distance_after_news",
                ]
            ))
        overview = {
            "symbol": symbol,
            "clusters": evaluated,
            "candidates": candidates,
            "buy_review": [item for item in candidates if item["action"] in {"STRONG_BUY_REVIEW", "BUY_REVIEW"}],
            "attention": [item for item in candidates if item["action"] in {"WATCH", "WAIT", "SELL_REVIEW"}],
            "provider_statuses": self.provider_statuses,
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "promoted_features": promoted,
            "feature_values": feature_values,
            "statistics_available": False,
            "automatic_order": False,
            "disclaimer": "뉴스·수급·가격 기반 매수 검토 목록이며 자동주문 또는 검증된 기대수익 모델이 아닙니다.",
        }
        self.overviews[symbol] = overview
        try:
            self.db.save_news_pipeline_state(self.articles, self.clusters, candidates, self.provider_statuses)
        except Exception as exc:
            logger.warning("뉴스 후보 DB 저장 실패(시세 기능은 계속): %s", type(exc).__name__)
        return overview

    def _feature_values(
        self, symbol: str, evaluated: list[dict[str, Any]], latest: dict[str, Any] | None, mode: str
    ) -> dict[str, dict[str, Any]]:
        now = utcnow()
        relevant = evaluated
        windows = {
            minutes: sum((now - parse_time(item["first_published_at"])).total_seconds() <= minutes * 60 for item in relevant)
            for minutes in (5, 15, 60)
        }
        values: dict[str, Any] = {key: None for key in self.feature_specs}
        values.update({
            "news_event_count_5m": windows[5],
            "news_event_count_15m": windows[15],
            "news_event_count_60m": windows[60],
            "unique_news_event_count": len(relevant),
            "raw_article_count": sum(item.get("raw_article_count", 0) for item in relevant),
            "verified_news_count": sum(item["verification"]["status"] in {"VERIFIED_PRIMARY", "VERIFIED_MULTI_SOURCE"} for item in relevant),
            "conflicted_news_count": sum(item["verification"]["status"] == "CONFLICTED" for item in relevant),
            "retracted_news_count": sum(item["verification"]["status"] == "RETRACTED" for item in relevant),
            "news_materiality_max": max((item["materiality"]["score"] for item in relevant), default=None),
            "news_materiality_mean": round(sum(item["materiality"]["score"] for item in relevant) / len(relevant), 1) if relevant else None,
        })
        dart_available = any(
            row.get("provider_id") == "dart_rss" and row.get("available") and row.get("state") != "ERROR"
            for row in self.provider_statuses
        )
        if dart_available:
            dart_keywords = {
                "dart_rights_issue": ("유상증자",),
                "dart_capital_reduction": ("감자",),
                "dart_cb_bw": ("전환사채", "신주인수권부사채", "CB", "BW"),
                "dart_major_holder": ("최대주주 변경", "최대주주변경"),
                "dart_embezzlement": ("횡령", "배임"),
                "dart_default": ("부도", "회생절차", "파산"),
                "dart_audit": ("감사의견", "감사보고서"),
                "dart_lawsuit": ("소송", "청구"),
            }
            dart_clusters = [
                item for item in relevant
                if any(
                    article.get("provider_id") in {"dart_rss", "open_dart_news"}
                    for article in item.get("articles", [])
                )
            ]
            for feature_id, keywords in dart_keywords.items():
                # Count independent event clusters, never republished article rows.
                values[feature_id] = sum(
                    any(keyword.lower() in str(item.get("canonical_title") or "").lower() for keyword in keywords)
                    for item in dart_clusters
                )
        if latest:
            evaluation = latest["evaluation"]
            reaction = evaluation["price_volume_reaction"]
            flow = evaluation["flow_confirmation"]
            verification = latest["verification"]
            sentiment = latest["sentiment"]
            values.update({
                "news_verification_status": verification["status"],
                "independent_source_count": verification["independent_source_count"],
                "primary_source_count": verification["primary_source_count"],
                "syndicated_copy_count": verification["syndicated_copy_count"],
                "source_diversity": latest.get("publisher_count"),
                "positive_news_score": 100 if sentiment["sentiment"] == "STRONG_POSITIVE" else 70 if sentiment["sentiment"] == "POSITIVE" else 0,
                "negative_news_score": 100 if sentiment["sentiment"] == "STRONG_NEGATIVE" else 70 if sentiment["sentiment"] == "NEGATIVE" else 0,
                "net_news_score": (sentiment["positive_keyword_count"] - sentiment["negative_keyword_count"]) * 10,
                "latest_news_age_seconds": round((now - parse_time(latest["first_published_at"])).total_seconds()),
                "news_price_confirmation": reaction["price_confirmation"],
                "news_volume_confirmation": reaction["volume_confirmation"],
                "news_institution_confirmation": flow["institution_confirmation"],
                "news_foreign_confirmation": flow["foreign_confirmation"],
                "news_member_flow_confirmation": flow["member_confirmation"],
                "news_already_priced_score": 90 if isinstance(reaction.get("return_after_news_5m"), (int, float)) and reaction["return_after_news_5m"] > 7 else 0,
                "news_contradiction_score": min(100, len(latest.get("contradictions", [])) * 50),
                "news_data_quality_score": evaluation["data_quality"],
                "member_buy_concentration_after_news": flow["member_buy_concentration_after_news"],
                "member_sell_concentration_after_news": flow["member_sell_concentration_after_news"],
                "foreign_institution_joint_buy_after_news": (
                    flow["institution_confirmation"] is True and flow["foreign_confirmation"] is True
                ) if flow["institution_confirmation"] is not None and flow["foreign_confirmation"] is not None else None,
                "foreign_institution_joint_sell_after_news": (
                    flow["institution_confirmation"] is False and flow["foreign_confirmation"] is False
                ) if flow["institution_confirmation"] is not None and flow["foreign_confirmation"] is not None else None,
                **{key: reaction.get(key) for key in reaction if key in values},
            })
        payload: dict[str, dict[str, Any]] = {}
        for feature_id, (label, unit, direction) in self.feature_specs.items():
            value = values.get(feature_id)
            quality = (
                "UNAVAILABLE" if value is None else
                "SIMULATED/DEMO" if mode == "demo" else
                "VERIFIED_NEWS_PIPELINE"
            )
            supplied_direction = direction
            if feature_id.startswith("news_") and value is None:
                supplied_direction = "unknown"
            payload[feature_id] = {
                "value": value,
                "label": label,
                "unit": unit,
                "direction": supplied_direction,
                "source": "DART 공식 RSS 사건 클러스터" if feature_id.startswith("dart_") else "뉴스 사건 클러스터·KIS 반응 계산",
                "quality": quality,
                "observed_at": now.isoformat() if value is not None else None,
                "metadata": {
                    "article_count_is_not_verification_count": True,
                    "institution_is_not_member_flow": True,
                    "statistics_available": False,
                    "automatic_order": False,
                },
            }
        return payload

    def feature_groups(self, overview: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
        values = overview.get("feature_values", {})
        institution_ids = [feature_id for feature_id in self.feature_specs if "institution" in feature_id or "member_" in feature_id or "foreign_" in feature_id or "pension_" in feature_id or "investment_trust_" in feature_id or "private_equity_" in feature_id]
        reaction_ids = [feature_id for feature_id in self.feature_specs if feature_id.startswith(("return_after_", "volume_ratio_", "turnover_", "vwap_", "high_", "low_", "spread_", "orderbook_", "upper_", "price_reversal_", "sector_", "peer_", "competitor_", "market_adjusted_"))]
        event_ids = [feature_id for feature_id in self.feature_specs if feature_id not in set(institution_ids + reaction_ids)]
        groups = {
            "뉴스 확인 기관·외국인·회원사": [dict(values[feature_id], key=feature_id) for feature_id in institution_ids],
            "뉴스 사실·원출처 검증": [dict(values[feature_id], key=feature_id) for feature_id in event_ids],
            "뉴스 이후 가격·거래량 확인": [dict(values[feature_id], key=feature_id) for feature_id in reaction_ids],
        }
        flat = {feature_id: item.get("value") for feature_id, item in values.items()}
        return groups, flat

    def overview(self, symbol: str) -> dict[str, Any]:
        return self.overviews.get(symbol, {
            "symbol": symbol, "clusters": [], "candidates": [], "buy_review": [], "attention": [],
            "provider_statuses": self.provider_statuses,
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "promoted_features": [], "feature_values": {},
            "statistics_available": False, "automatic_order": False,
            "disclaimer": "뉴스·수급·가격 기반 매수 검토 목록이며 자동주문 또는 검증된 기대수익 모델이 아닙니다.",
        })
