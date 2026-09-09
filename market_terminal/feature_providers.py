from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import re
import threading
import time
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests


@dataclass(slots=True)
class ProviderStatus:
    provider_id: str
    available: bool
    state: str
    message: str
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(slots=True)
class FeatureRequest:
    symbol: str
    feature_ids: set[str]
    as_of: str


@dataclass(slots=True)
class FeatureBatch:
    provider_id: str
    features: dict[str, dict[str, Any]]
    fetched_at: str
    errors: list[str] = field(default_factory=list)


class FeatureDataProvider(ABC):
    provider_id: str
    cache_ttl_seconds: float = 60.0

    @abstractmethod
    async def health_check(self) -> ProviderStatus: ...

    @abstractmethod
    async def fetch(self, request: FeatureRequest) -> FeatureBatch: ...

    @abstractmethod
    def supported_features(self) -> set[str]: ...


class UnavailableProvider(FeatureDataProvider):
    def __init__(self, provider_id: str, reason: str, supported: set[str] | None = None):
        self.provider_id = provider_id
        self.reason = reason
        self._supported = supported or set()

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(self.provider_id, False, "UNAVAILABLE", self.reason)

    async def fetch(self, request: FeatureRequest) -> FeatureBatch:
        return FeatureBatch(self.provider_id, {}, datetime.now(timezone.utc).isoformat(), [self.reason])

    def supported_features(self) -> set[str]:
        return set(self._supported)


class StatusProvider(FeatureDataProvider):
    """Health/status entry for feeds consumed directly by MarketProvider."""

    def __init__(self, provider_id: str, state: str, message: str, supported: set[str] | None = None):
        self.provider_id = provider_id
        self.state = state
        self.message = message
        self._supported = supported or set()

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(self.provider_id, True, self.state, self.message)

    async def fetch(self, request: FeatureRequest) -> FeatureBatch:
        return FeatureBatch(self.provider_id, {}, datetime.now(timezone.utc).isoformat())

    def supported_features(self) -> set[str]:
        return set(self._supported)


class ManualJsonProvider(FeatureDataProvider):
    provider_id = "manual_json"
    cache_ttl_seconds = 2.0

    def __init__(self, path: Path):
        self.path = path

    async def health_check(self) -> ProviderStatus:
        if not self.path.exists():
            return ProviderStatus(self.provider_id, False, "UNAVAILABLE", f"{self.path.name} 없음")
        try:
            json.loads(self.path.read_text(encoding="utf-8"))
            return ProviderStatus(self.provider_id, True, "MANUAL", "수동 JSON 사용 가능")
        except (OSError, ValueError) as exc:
            return ProviderStatus(self.provider_id, False, "ERROR", str(exc))

    async def fetch(self, request: FeatureRequest) -> FeatureBatch:
        now = datetime.now(timezone.utc).isoformat()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            merged = dict(payload.get("global", {}))
            merged.update(payload.get("symbols", {}).get(request.symbol, {}))
            features: dict[str, dict[str, Any]] = {}
            for feature_id in request.feature_ids:
                if feature_id not in merged:
                    continue
                raw = merged[feature_id]
                item = dict(raw) if isinstance(raw, dict) else {"value": raw}
                item.setdefault("source", "수동 JSON")
                item.setdefault("source_type", "MANUAL")
                item.setdefault("observed_at", now)
                item.setdefault("collected_at", now)
                item.setdefault("quality", "MANUAL_OVERRIDE")
                item.setdefault("metadata", {"is_proxy": False, "frequency": "사용자 갱신"})
                features[feature_id] = item
            return FeatureBatch(self.provider_id, features, now)
        except (OSError, ValueError) as exc:
            return FeatureBatch(self.provider_id, {}, now, [str(exc)])

    def supported_features(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            ids = set(payload.get("global", {}))
            for values in payload.get("symbols", {}).values():
                ids.update(values)
            return ids
        except (OSError, ValueError):
            return set()


class CsvUploadProvider(FeatureDataProvider):
    provider_id = "csv_upload"
    cache_ttl_seconds = 3.0

    def __init__(self, directory: Path):
        self.directory = directory

    async def health_check(self) -> ProviderStatus:
        files = list(self.directory.glob("*.csv")) if self.directory.exists() else []
        return ProviderStatus(
            self.provider_id,
            bool(files),
            "MANUAL" if files else "UNAVAILABLE",
            f"CSV {len(files)}개" if files else "data/import에 CSV 없음",
        )

    async def fetch(self, request: FeatureRequest) -> FeatureBatch:
        now = datetime.now(timezone.utc).isoformat()
        features: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for path in self.directory.glob("*.csv") if self.directory.exists() else []:
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        if row.get("symbol") not in {"", request.symbol, None}:
                            continue
                        feature_id = str(row.get("feature_id") or "")
                        if feature_id not in request.feature_ids:
                            continue
                        raw = row.get("value")
                        try:
                            value: Any = float(raw) if raw not in (None, "") else None
                        except ValueError:
                            value = raw
                        features[feature_id] = {
                            "value": value,
                            "source": f"CSV:{path.name}",
                            "source_type": "MANUAL",
                            "observed_at": row.get("observed_at") or now,
                            "collected_at": now,
                            "quality": "CSV_IMPORT",
                            "metadata": {"is_proxy": False, "frequency": "사용자 파일 갱신"},
                        }
            except (OSError, ValueError) as exc:
                errors.append(f"{path.name}: {exc}")
        return FeatureBatch(self.provider_id, features, now, errors)

    def supported_features(self) -> set[str]:
        return set()


class OpenDartProvider(FeatureDataProvider):
    """Read-only DART disclosure adapter. It never sends an order or write."""

    provider_id = "open_dart"
    cache_ttl_seconds = 900.0
    base_url = "https://opendart.fss.or.kr/api"
    disclosure_features = {
        "dart_supply_contract", "dart_rights_issue", "dart_bond_borrowing",
        "dart_treasury_dividend", "dart_new_facility", "large_supply_contract",
        "subsidiary_ma_disclosure", "mine_brine_investment_disclosure",
        "dart_capital_reduction", "dart_cb_bw", "dart_major_holder",
        "dart_embezzlement", "dart_default", "dart_audit", "dart_lawsuit",
    }
    financial_features = {
        "revenue", "operating_profit", "net_income", "gross_profit",
        "operating_margin", "net_margin", "gross_margin",
        "operating_cashflow", "investing_cashflow", "financing_cashflow",
        "cashflow", "capex", "depreciation", "current_assets", "current_liabilities", "current_ratio",
        "cash_and_cash_equivalents", "short_term_borrowings", "long_term_borrowings",
        "bonds", "net_debt", "accounts_receivable", "inventory",
        "contract_assets", "contract_liabilities", "total_assets", "total_equity",
        "total_liabilities", "debt_ratio", "roa", "roe", "cash_assets", "borrow_assets",
    }
    account_candidates = {
        "revenue": ("ifrs-full_Revenue", "매출액", "영업수익", "수익(매출액)"),
        "operating_profit": ("dart_OperatingIncomeLoss", "영업이익", "영업이익(손실)"),
        "net_income": ("ifrs-full_ProfitLoss", "당기순이익", "당기순이익(손실)", "연결당기순이익"),
        "gross_profit": ("ifrs-full_GrossProfit", "매출총이익"),
        "operating_cashflow": ("ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름"),
        "investing_cashflow": ("ifrs-full_CashFlowsFromUsedInInvestingActivities", "투자활동현금흐름"),
        "financing_cashflow": ("ifrs-full_CashFlowsFromUsedInFinancingActivities", "재무활동현금흐름"),
        "capex": ("ifrs-full_PaymentsToAcquirePropertyPlantAndEquipment", "유형자산의취득", "유형자산 취득"),
        "depreciation": ("ifrs-full_DepreciationDepletionAndAmortization", "감가상각비"),
        "current_assets": ("ifrs-full_CurrentAssets", "유동자산"),
        "current_liabilities": ("ifrs-full_CurrentLiabilities", "유동부채"),
        "cash_and_cash_equivalents": ("ifrs-full_CashAndCashEquivalents", "현금및현금성자산", "현금 및 현금성자산"),
        "short_term_borrowings": ("dart_ShortTermBorrowings", "단기차입금"),
        "long_term_borrowings": ("dart_LongTermBorrowingsGross", "장기차입금"),
        "bonds": ("dart_BondsIssued", "사채", "회사채"),
        "accounts_receivable": ("ifrs-full_TradeAndOtherCurrentReceivables", "매출채권", "매출채권및기타채권"),
        "inventory": ("ifrs-full_Inventories", "재고자산"),
        "contract_assets": ("ifrs-full_ContractAssets", "계약자산"),
        "contract_liabilities": ("ifrs-full_ContractLiabilities", "계약부채"),
        "total_assets": ("ifrs-full_Assets", "자산총계"),
        "total_equity": ("ifrs-full_Equity", "자본총계"),
        "total_liabilities": ("ifrs-full_Liabilities", "부채총계"),
    }

    def __init__(self, api_key: str, cache_file: Path):
        self.api_key = api_key
        self.cache_file = cache_file
        self._corp_codes: dict[str, str] | None = None

    async def health_check(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(self.provider_id, False, "UNAVAILABLE", "OPEN_DART_API_KEY 미설정")
        return ProviderStatus(self.provider_id, True, "PERIODIC", "DART 키 설정됨")

    def supported_features(self) -> set[str]:
        return set(self.disclosure_features | self.financial_features)

    async def fetch(self, request: FeatureRequest) -> FeatureBatch:
        if not self.api_key:
            return FeatureBatch(self.provider_id, {}, datetime.now(timezone.utc).isoformat(), ["OPEN_DART_API_KEY 미설정"])
        return await asyncio.to_thread(self._fetch_sync, request)

    def _load_corp_codes(self) -> dict[str, str]:
        if self._corp_codes is not None:
            return self._corp_codes
        xml_bytes: bytes
        if self.cache_file.exists():
            xml_bytes = self.cache_file.read_bytes()
        else:
            response = requests.get(f"{self.base_url}/corpCode.xml", params={"crtfc_key": self.api_key}, timeout=30)
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                xml_bytes = archive.read("CORPCODE.xml")
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_bytes(xml_bytes)
        root = ElementTree.fromstring(xml_bytes)
        self._corp_codes = {
            (item.findtext("stock_code") or "").strip(): (item.findtext("corp_code") or "").strip()
            for item in root.findall("list")
            if (item.findtext("stock_code") or "").strip()
        }
        return self._corp_codes

    def _fetch_sync(self, request: FeatureRequest) -> FeatureBatch:
        now = datetime.now(timezone.utc)
        try:
            corp_code = self._load_corp_codes().get(request.symbol)
            if not corp_code:
                raise RuntimeError("DART 종목 코드 매핑 없음")
            response = requests.get(
                f"{self.base_url}/list.json",
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bgn_de": now.strftime("%Y0101"),
                    "end_de": now.strftime("%Y%m%d"),
                    "page_count": "100",
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            status = str(payload.get("status") or "000")
            if status not in {"000", "013"}:
                raise RuntimeError(f"OpenDART 공시검색 오류 코드 {status}")
            reports = payload.get("list", []) if status == "000" else []
            keywords = {
                "dart_supply_contract": ("공급계약", "단일판매"),
                "dart_rights_issue": ("유상증자",),
                "dart_bond_borrowing": ("회사채", "차입"),
                "dart_treasury_dividend": ("자기주식", "배당"),
                "dart_new_facility": ("신규시설투자",),
                "large_supply_contract": ("단일판매", "공급계약"),
                "subsidiary_ma_disclosure": ("타법인", "합병", "양수"),
                "mine_brine_investment_disclosure": ("광산", "염호", "소재투자"),
                "dart_capital_reduction": ("감자",),
                "dart_cb_bw": ("전환사채", "신주인수권부사채", "CB", "BW"),
                "dart_major_holder": ("최대주주 변경", "최대주주변경"),
                "dart_embezzlement": ("횡령", "배임"),
                "dart_default": ("부도", "회생절차", "파산"),
                "dart_audit": ("감사의견", "감사보고서"),
                "dart_lawsuit": ("소송", "청구"),
            }
            features: dict[str, dict[str, Any]] = {}
            for feature_id in request.feature_ids & self.disclosure_features:
                count = sum(any(word in str(row.get("report_nm", "")) for word in keywords[feature_id]) for row in reports)
                features[feature_id] = {
                    "value": count,
                    "source": "OpenDART 공시검색",
                    "source_type": "PERIODIC",
                    "observed_at": now.isoformat(),
                    "collected_at": now.isoformat(),
                    "quality": "공식 공시검색 / 당해연도 누적 건수",
                    "reliability": 0.95,
                    "metadata": {"is_proxy": False, "frequency": "15분 캐시", "delay_possible": False},
                }
            if request.feature_ids & self.financial_features:
                features.update(self._financial_features(corp_code, request.feature_ids, now))
            return FeatureBatch(self.provider_id, features, now.isoformat())
        except Exception as exc:
            return FeatureBatch(self.provider_id, {}, now.isoformat(), [str(exc)])

    @staticmethod
    def _amount(value: Any) -> float | None:
        if value in (None, "", "-"):
            return None
        text = str(value).strip().replace(",", "")
        negative = text.startswith("(") and text.endswith(")")
        text = re.sub(r"[^0-9.+-]", "", text)
        try:
            number = float(text)
            return -number if negative else number
        except ValueError:
            return None

    def _latest_financial_rows(self, corp_code: str, now: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # The returned income/cash-flow values remain report-period cumulative.
        # They are never relabelled as a stand-alone quarter.
        for year in (now.year, now.year - 1, now.year - 2):
            for report_code, report_name in (("11014", "3분기 누적"), ("11012", "반기 누적"), ("11013", "1분기 누적"), ("11011", "사업연도")):
                for fs_div in ("CFS", "OFS"):
                    response = requests.get(
                        f"{self.base_url}/fnlttSinglAcntAll.json",
                        params={
                            "crtfc_key": self.api_key, "corp_code": corp_code,
                            "bsns_year": str(year), "reprt_code": report_code, "fs_div": fs_div,
                        },
                        headers={"User-Agent": "KR-DayTrading-Terminal/3.0"}, timeout=(8, 25),
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if str(payload.get("status") or "") == "000" and payload.get("list"):
                        return list(payload["list"]), {
                            "business_year": year, "report_code": report_code,
                            "report_name": report_name, "fs_div": fs_div,
                        }
        return [], {}

    def _financial_features(self, corp_code: str, requested: set[str], now: datetime) -> dict[str, dict[str, Any]]:
        rows, report = self._latest_financial_rows(corp_code, now)
        if not rows:
            return {}

        def find(feature_id: str) -> float | None:
            candidates = self.account_candidates[feature_id]
            exact: list[float] = []
            contains: list[float] = []
            for row in rows:
                account_id = str(row.get("account_id") or "").strip()
                account_name = re.sub(r"\s+", "", str(row.get("account_nm") or ""))
                value = self._amount(row.get("thstrm_amount"))
                if value is None:
                    continue
                for candidate in candidates:
                    normalized = re.sub(r"\s+", "", candidate)
                    if account_id == candidate or account_name == normalized:
                        exact.append(value)
                        break
                    if len(normalized) >= 4 and normalized in account_name:
                        contains.append(value)
                        break
            return exact[0] if exact else contains[0] if contains else None

        raw = {feature_id: find(feature_id) for feature_id in self.account_candidates}

        def total(*keys: str) -> float | None:
            values = [raw.get(key) for key in keys]
            present = [value for value in values if value is not None]
            return sum(present) if present else None

        borrowings = total("short_term_borrowings", "long_term_borrowings", "bonds")
        derived: dict[str, float | None] = {
            **raw,
            "net_debt": (borrowings - raw["cash_and_cash_equivalents"]) if borrowings is not None and raw["cash_and_cash_equivalents"] is not None else None,
            "operating_margin": raw["operating_profit"] / raw["revenue"] * 100 if raw["operating_profit"] is not None and raw["revenue"] else None,
            "net_margin": raw["net_income"] / raw["revenue"] * 100 if raw["net_income"] is not None and raw["revenue"] else None,
            "gross_margin": raw["gross_profit"] / raw["revenue"] * 100 if raw["gross_profit"] is not None and raw["revenue"] else None,
            "debt_ratio": raw["total_liabilities"] / raw["total_equity"] * 100 if raw["total_liabilities"] is not None and raw["total_equity"] else None,
            "roa": raw["net_income"] / raw["total_assets"] * 100 if raw["net_income"] is not None and raw["total_assets"] else None,
            "roe": raw["net_income"] / raw["total_equity"] * 100 if raw["net_income"] is not None and raw["total_equity"] else None,
            "cash_assets": raw["cash_and_cash_equivalents"] / raw["total_assets"] * 100 if raw["cash_and_cash_equivalents"] is not None and raw["total_assets"] else None,
            "borrow_assets": borrowings / raw["total_assets"] * 100 if borrowings is not None and raw["total_assets"] else None,
            "current_ratio": raw["current_assets"] / raw["current_liabilities"] * 100 if raw["current_assets"] is not None and raw["current_liabilities"] else None,
            "cashflow": raw["operating_cashflow"],
            "capex": abs(raw["capex"]) if raw["capex"] is not None else None,
        }
        observed = next((str(row.get("thstrm_dt")) for row in rows if row.get("thstrm_dt")), str(report["business_year"]))
        output: dict[str, dict[str, Any]] = {}
        for feature_id in requested & self.financial_features:
            value = derived.get(feature_id)
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                continue
            output[feature_id] = {
                "value": round(value, 4), "source": "OpenDART 정기보고서 XBRL",
                "source_type": "PERIODIC", "observed_at": observed,
                "collected_at": now.isoformat(),
                "quality": f"{report['report_name']} 원값 · {'연결' if report['fs_div'] == 'CFS' else '별도'}재무제표",
                "reliability": 0.96,
                "metadata": {
                    "is_proxy": False, "frequency": "분기/연간",
                    "delay_possible": True, "period_basis": report["report_name"],
                    "business_year": report["business_year"], "report_code": report["report_code"],
                    "fs_div": report["fs_div"], "single_quarter_derived": False,
                },
            }
        return output


class EcosProvider(FeatureDataProvider):
    """Official Bank of Korea key-statistics adapter (read-only)."""

    provider_id = "ecos"
    cache_ttl_seconds = 1800.0
    supported = {"bok_rate", "bok_base_rate", "usdkrw", "usdkrw_change", "ecos_house", "house_price"}

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def health_check(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(self.provider_id, False, "UNAVAILABLE", "ECOS_API_KEY 미설정")
        return ProviderStatus(self.provider_id, True, "PERIODIC", "ECOS 공식 통계 키 설정됨")

    def supported_features(self) -> set[str]:
        return set(self.supported)

    async def fetch(self, request: FeatureRequest) -> FeatureBatch:
        if not self.api_key:
            return FeatureBatch(self.provider_id, {}, datetime.now(timezone.utc).isoformat(), ["ECOS_API_KEY 미설정"])
        return await asyncio.to_thread(self._fetch_sync, request)

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            result = float(str(value).replace(",", "").strip())
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    def _fetch_sync(self, request: FeatureRequest) -> FeatureBatch:
        now = datetime.now(timezone.utc).isoformat()
        try:
            response = requests.get(
                f"https://ecos.bok.or.kr/api/KeyStatisticList/{self.api_key}/json/kr/1/100",
                headers={"User-Agent": "KR-DayTrading-Terminal/3.0"}, timeout=(8, 25),
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("RESULT"):
                result = payload["RESULT"]
                raise RuntimeError(f"ECOS {result.get('CODE', 'ERROR')}: {result.get('MESSAGE', '응답 오류')}")
            rows = payload.get("KeyStatisticList", {}).get("row", [])
            if not isinstance(rows, list):
                raise RuntimeError("ECOS 응답 구조가 변경되었습니다")

            features: dict[str, dict[str, Any]] = {}

            def publish(feature_ids: tuple[str, ...], row: dict[str, Any]) -> None:
                value = self._number(row.get("DATA_VALUE"))
                if value is None:
                    return
                for feature_id in feature_ids:
                    if feature_id not in request.feature_ids:
                        continue
                    features[feature_id] = {
                        "value": value,
                        "source": "한국은행 ECOS 주요경제지표",
                        "source_type": "PERIODIC",
                        "observed_at": str(row.get("TIME") or request.as_of),
                        "collected_at": now,
                        "quality": f"공식 통계 / {row.get('CYCLE') or '주기 미표기'}",
                        "reliability": 0.96,
                        "metadata": {
                            "is_proxy": False,
                            "frequency": row.get("CYCLE"),
                            "unit": row.get("UNIT_NAME"),
                            "stat_name": row.get("KEYSTAT_NAME"),
                            "delay_possible": True,
                        },
                    }

            for row in rows:
                name = re.sub(r"\s+", "", str(row.get("KEYSTAT_NAME") or ""))
                if "한국은행기준금리" in name or name == "기준금리":
                    publish(("bok_rate", "bok_base_rate"), row)
                elif ("원/미국달러" in name or "원달러" in name or "대미달러" in name) and "환율" in name:
                    publish(("usdkrw",), row)
                elif "주택" in name and ("매매가격" in name or "가격지수" in name):
                    publish(("ecos_house", "house_price"), row)
            return FeatureBatch(self.provider_id, features, now)
        except Exception as exc:
            return FeatureBatch(self.provider_id, {}, now, [str(exc)])


class ProviderHub:
    def __init__(self, providers: list[FeatureDataProvider]):
        self.providers = providers
        self._cache: dict[tuple[str, str], tuple[float, FeatureBatch]] = {}
        self._lock = threading.RLock()
        self.last_diagnostics: list[dict[str, Any]] = []

    async def health(self) -> list[dict[str, Any]]:
        statuses = await asyncio.gather(*(provider.health_check() for provider in self.providers), return_exceptions=True)
        return [
            {
                "provider_id": item.provider_id if isinstance(item, ProviderStatus) else self.providers[index].provider_id,
                "available": item.available if isinstance(item, ProviderStatus) else False,
                "state": item.state if isinstance(item, ProviderStatus) else "ERROR",
                "message": item.message if isinstance(item, ProviderStatus) else f"상태 검사 실패({type(item).__name__})",
                "checked_at": item.checked_at if isinstance(item, ProviderStatus) else datetime.now(timezone.utc).isoformat(),
            }
            for index, item in enumerate(statuses)
        ]

    async def _fetch_one(self, provider: FeatureDataProvider, request: FeatureRequest) -> FeatureBatch:
        key = (provider.provider_id, request.symbol)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
        if cached and now - cached[0] < provider.cache_ttl_seconds:
            return cached[1]
        try:
            batch = await asyncio.wait_for(provider.fetch(request), timeout=45)
            if batch.features:
                with self._lock:
                    self._cache[key] = (now, batch)
            return batch
        except Exception as exc:
            if cached:
                stale_features = {}
                for feature_id, raw in cached[1].features.items():
                    item = dict(raw)
                    item["freshness"] = "STALE"
                    item["quality"] = f"{item.get('quality', '')} / STALE CACHE".strip(" /")
                    metadata = dict(item.get("metadata") or {})
                    metadata.update({"stale": True, "refresh_error": type(exc).__name__})
                    item["metadata"] = metadata
                    stale_features[feature_id] = item
                return FeatureBatch(provider.provider_id, stale_features, cached[1].fetched_at, [f"캐시 대체: {type(exc).__name__}"])
            return FeatureBatch(provider.provider_id, {}, datetime.now(timezone.utc).isoformat(), [type(exc).__name__])

    async def fetch(self, request: FeatureRequest) -> dict[str, dict[str, Any]]:
        batches = await asyncio.gather(*(self._fetch_one(provider, request) for provider in self.providers))
        merged: dict[str, dict[str, Any]] = {}
        diagnostics: list[dict[str, Any]] = []
        for batch in batches:
            diagnostics.append({
                "provider_id": batch.provider_id, "feature_count": len(batch.features),
                "fetched_at": batch.fetched_at, "errors": batch.errors,
            })
            for feature_id, raw in batch.features.items():
                value = raw.get("value") if isinstance(raw, dict) else raw
                if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
                    continue
                if isinstance(value, (list, dict)) and not value:
                    continue
                merged[feature_id] = dict(raw) if isinstance(raw, dict) else {"value": raw}
        self.last_diagnostics = diagnostics
        return merged

    def fetch_blocking(self, request: FeatureRequest) -> dict[str, dict[str, Any]]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.fetch(request))

        # build_snapshot is also called by async FastAPI routes.  A nested
        # asyncio.run() would fail there, so isolate the provider loop in one
        # short-lived worker thread while keeping this compatibility API sync.
        result: dict[str, dict[str, Any]] = {}
        failure: list[BaseException] = []

        def runner() -> None:
            try:
                result.update(asyncio.run(self.fetch(request)))
            except BaseException as exc:  # propagate on the caller thread
                failure.append(exc)

        worker = threading.Thread(target=runner, name="feature-provider-loop", daemon=True)
        worker.start()
        worker.join()
        if failure:
            raise failure[0]
        return result
