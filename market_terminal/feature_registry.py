from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import ASSET_ROOT

GOOD = "#ff4d5a"
BAD = "#4388ff"
NEUTRAL = "#d8e1ec"
CAUTION = "#ffad42"
UNKNOWN = "#68788a"


def unavailable_guidance(feature_id: str, group: str, required_api: str | None) -> tuple[str, str]:
    """Return an honest, visible next step for a missing live feature."""
    if feature_id in {"night_futures_change", "night_futures_gap"}:
        return (
            "KIS 국내선물 전광판은 장외·야간 KOSPI200 선물 실시간 값을 제공하지 않습니다.",
            "KIS 해외선물옵션 이용 권한과 해당 야간 계약 API 또는 KRX 유료 실시간 시세를 연결해야 합니다.",
        )
    if feature_id == "futures_open_interest_change":
        return (
            "KIS 전광판의 원본 미결제약정 증감값이 비어 있거나 첫 수신값입니다.",
            "다음 수신부터 직전 수신값 대비로 계산합니다. 거래소 기준 증감은 KIS 상세 시세 권한이 필요합니다.",
        )
    if feature_id.startswith(("return_after_news_", "institution_net_buy_after_news", "foreign_net_buy_after_news", "institution_flow_", "foreign_flow_", "member_", "program_net_buy_after_news", "financial_investment_flow_after_news", "news_")) or "뉴스" in group:
        return (
            "현재 종목에 기준 시각을 확정할 검증 뉴스 사건이 없습니다.",
            "새 뉴스가 수집·검증된 뒤 해당 시각 이후의 가격·수급 데이터를 누적하면 자동 계산됩니다.",
        )
    if feature_id in {"idio_vol", "global_beta", "global_corr", "crash_sensitivity", "cross_section_pct"}:
        return (
            "계산에 필요한 장기 가격 이력 또는 전 종목 단면 데이터가 아직 충분하지 않습니다.",
            "20~60거래일 가격 이력과 시장·동종주 유니버스를 누적한 뒤 계산해야 하며, 단일 실시간 API 값으로 대체할 수 없습니다.",
        )
    if feature_id in {"turnover", "short_ratio", "short_balance_to_cap", "short_cumulative", "price_short_divergence", "stock_lending_balance"}:
        return (
            "KIS 시세 응답에 발행주식수·공매도·대차 원본 데이터가 포함되지 않습니다.",
            "KRX 공매도·대차 공시 또는 라이선스된 수급 데이터 공급원을 연결해야 합니다.",
        )
    if feature_id in {"program_trading", "program_arbitrage_net", "program_non_arbitrage_net", "put_call_ratio", "option_implied_volatility", "vkospi"}:
        return (
            "현재 연결된 공급원에서 이 파생·프로그램 원본 값을 받지 못했습니다.",
            "KIS 해당 상세 시세 권한 또는 KRX 파생상품 실시간 데이터 공급원을 연결해야 합니다.",
        )
    if required_api:
        return (
            f"{required_api} 공급원에서 현재 값이 반환되지 않았습니다.",
            f"{required_api} 서비스 권한·종목 매핑·데이터 제공 시간을 확인해야 합니다.",
        )
    return (
        "현재 연결된 공급원에서 이 피처의 원본 값을 제공하지 않습니다.",
        "공식 원출처 또는 라이선스된 데이터 공급원을 추가로 연결해야 합니다.",
    )


class FeaturePolarityResolver:
    """Resolve semantic direction; never color a feature from its sign alone."""

    def resolve(
        self,
        definition: dict[str, Any],
        value: Any,
        *,
        symbol_profile: dict[str, Any] | None = None,
        market_regime: str | None = None,
        related: dict[str, Any] | None = None,
        supplied_direction: str | None = None,
    ) -> tuple[str, str]:
        if value is None:
            return "UNKNOWN", "데이터 미연결 또는 값 없음"
        supplied = {
            "positive": "POSITIVE",
            "negative": "NEGATIVE",
            "neutral": "NEUTRAL",
            "warning": "CAUTION",
            "conflicted": "CONFLICTED",
        }.get(str(supplied_direction or "").lower())
        if supplied:
            return supplied, "실시간 계산 피처의 의미 방향"

        polarity = definition.get("polarity", "NEUTRAL")
        if not isinstance(value, (int, float)):
            return "NEUTRAL", "숫자 방향을 적용하지 않는 상태 피처"
        if polarity == "HIGH_GOOD":
            return ("POSITIVE" if value > 0 else "NEGATIVE" if value < 0 else "NEUTRAL"), "높을수록 긍정적인 피처"
        if polarity == "LOW_GOOD":
            return ("NEGATIVE" if value > 0 else "POSITIVE" if value < 0 else "NEUTRAL"), "낮을수록 긍정적인 피처"
        if polarity == "RANGE_GOOD":
            return "NEUTRAL", "적정 범위가 필요한 피처"
        if polarity == "CONTEXT_DEPENDENT":
            feature_id = definition.get("feature_id", "")
            related = related or {}
            profile = (symbol_profile or {}).get("profile", "")
            if feature_id in {"usdkrw", "eurkrw"} and profile in {"automobile_exporter", "semiconductor_memory"}:
                foreign = related.get("foreign_net")
                if isinstance(foreign, (int, float)) and foreign < 0:
                    return "CONFLICTED", "수출 채산성에는 유리할 수 있으나 외국인 매도와 충돌"
                return "CAUTION", "수출 효과와 외국인 수급 효과를 함께 확인해야 함"
            if feature_id in {"bok_base_rate", "kr3y", "kr10y", "bank_bond_rate"} and profile == "bank_financial":
                delinquency = related.get("delinquency_rate")
                if isinstance(delinquency, (int, float)) and delinquency > 0:
                    return "CONFLICTED", "NIM 개선 가능성과 신용비용 위험이 충돌"
                return "CAUTION", "NIM과 대손비용을 함께 확인해야 함"
            if feature_id in {"lithium_price", "lithium_carbonate_change", "lithium_hydroxide_change"}:
                inventory = related.get("raw_material_inventory")
                if value < 0 and isinstance(inventory, (int, float)) and inventory > 0:
                    return "CONFLICTED", "원가 하락과 재고평가손실 가능성이 충돌"
            return "CAUTION", f"시장 체제({market_regime or '미확정'})와 종목 노출을 함께 해석해야 함"
        return "NEUTRAL", "방향성이 없는 피처"


class FeatureRegistry:
    KIS_REQUIRED = {
        "kospi200_futures_price", "kospi200_futures_change", "futures_basis",
        "futures_open_interest", "futures_open_interest_change", "futures_volume",
        "night_futures_change", "foreign_net_buy_estimate", "institution_net_buy_estimate",
    }
    OPEN_DART_REQUIRED = {
        "dart_supply_contract", "dart_rights_issue", "dart_bond_borrowing",
        "dart_treasury_dividend", "dart_new_facility", "large_supply_contract",
        "subsidiary_ma_disclosure", "mine_brine_investment_disclosure",
        "dart_capital_reduction", "dart_cb_bw", "dart_major_holder", "dart_embezzlement",
        "dart_default", "dart_audit", "dart_lawsuit", "revenue", "operating_profit",
        "net_income", "gross_profit", "operating_margin", "net_margin", "gross_margin",
        "operating_cashflow", "investing_cashflow", "financing_cashflow",
        "cashflow", "capex", "depreciation", "current_assets", "current_liabilities", "current_ratio",
        "cash_and_cash_equivalents", "short_term_borrowings", "long_term_borrowings",
        "bonds", "net_debt", "accounts_receivable", "inventory", "contract_assets",
        "contract_liabilities", "total_assets", "total_equity", "total_liabilities",
        "debt_ratio", "roa", "roe", "cash_assets", "borrow_assets",
    }
    ECOS_REQUIRED = {"bok_base_rate", "house_price", "ecos_house"}

    def __init__(self, config_root: Path | None = None):
        root = config_root or (ASSET_ROOT / "config")
        self.config_root = root
        self.catalog_payload = self._load(root / "feature_catalog.yaml")
        self.profile_payload = self._load(root / "symbol_profiles.yaml")
        self.definitions: dict[str, dict[str, Any]] = {}
        self.profile_feature_ids: dict[str, list[str]] = {}
        self.group_names: dict[str, str] = {}
        self.resolver = FeaturePolarityResolver()
        self._index()

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def _index(self) -> None:
        for group_id, group in self.catalog_payload.get("groups", {}).items():
            self.group_names[group_id] = group.get("display_name", group_id)
            for raw in group.get("features", []):
                if not isinstance(raw, list) or len(raw) < 4:
                    continue
                feature_id, display_name, unit, polarity = raw[:4]
                self.definitions[str(feature_id)] = {
                    "feature_id": str(feature_id),
                    "display_name": str(display_name),
                    "group_id": group_id,
                    "group": self.group_names[group_id],
                    "unit": str(unit or ""),
                    "polarity": str(polarity),
                    "description": str(display_name),
                }
        profiles = self.profile_payload.get("profiles", {})
        for profile_id, profile in profiles.items():
            ids: list[str] = []
            for group_id in profile.get("feature_groups", []):
                group = self.catalog_payload.get("groups", {}).get(group_id, {})
                ids.extend(str(row[0]) for row in group.get("features", []) if isinstance(row, list) and row)
            self.profile_feature_ids[profile_id] = list(dict.fromkeys(ids))

    def profile(self, symbol: str) -> dict[str, Any]:
        value = self.profile_payload.get("symbols", {}).get(symbol)
        if value:
            return dict(value)
        return {
            "name": symbol,
            "market": "KOSPI",
            "sector": "기타",
            "industry": "미분류",
            "profile": "default",
            "peers_domestic": [],
            "peers_global": [],
        }

    def definition(self, feature_id: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        if feature_id in self.definitions:
            return dict(self.definitions[feature_id])
        fallback = fallback or {}
        return {
            "feature_id": feature_id,
            "display_name": fallback.get("label", feature_id),
            "group": fallback.get("group", "기존 실시간 피처"),
            "group_id": "existing",
            "unit": fallback.get("unit", ""),
            "polarity": "NEUTRAL",
            "description": fallback.get("label", feature_id),
        }

    def enrich(
        self,
        symbol: str,
        groups: dict[str, list[dict[str, Any]]],
        flat: dict[str, Any],
        external: dict[str, Any],
        mode: str,
        observed_at: str,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], list[dict[str, Any]]]:
        profile = self.profile(symbol)
        related = {**flat}
        now = datetime.now(timezone.utc).isoformat()
        output: dict[str, list[dict[str, Any]]] = {}
        seen: set[str] = set()

        for group_name, items in groups.items():
            normalized: list[dict[str, Any]] = []
            for item in items:
                feature_id = str(item.get("feature_id") or item.get("key") or "")
                if not feature_id or feature_id in seen:
                    continue
                seen.add(feature_id)
                definition = self.definition(feature_id, {**item, "group": group_name})
                status, explanation = self.resolver.resolve(
                    definition,
                    item.get("value"),
                    symbol_profile=profile,
                    related=related,
                    supplied_direction=item.get("direction"),
                )
                normalized.append(self._standardize(
                    feature_id, definition, symbol, item.get("value"), item,
                    status, explanation, observed_at, now, mode,
                ))
            if normalized:
                output[group_name] = normalized

        profile_id = profile.get("profile", "default")
        by_group: dict[str, list[dict[str, Any]]] = {}
        for feature_id in self.profile_feature_ids.get(profile_id, []):
            if feature_id in seen:
                continue
            definition = self.definitions[feature_id]
            raw = external.get(feature_id)
            value = raw.get("value") if isinstance(raw, dict) else raw
            item = raw if isinstance(raw, dict) else {}
            status, explanation = self.resolver.resolve(
                definition, value, symbol_profile=profile, related={**related, feature_id: value},
            )
            standardized = self._standardize(
                feature_id, definition, symbol, value, item, status, explanation,
                str(item.get("observed_at") or observed_at), now, mode,
            )
            by_group.setdefault(definition["group"], []).append(standardized)
            flat[feature_id] = value
            seen.add(feature_id)
        for group_name, items in by_group.items():
            output.setdefault(group_name, []).extend(items)

        flattened = [item for items in output.values() for item in items]
        return output, flat, flattened

    @staticmethod
    def _standardize(
        feature_id: str,
        definition: dict[str, Any],
        symbol: str,
        value: Any,
        raw: dict[str, Any],
        status: str,
        explanation: str,
        observed_at: str,
        received_at: str,
        mode: str,
    ) -> dict[str, Any]:
        numeric = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        previous = raw.get("previous_value")
        delta = numeric - float(previous) if numeric is not None and isinstance(previous, (int, float)) else None
        raw_metadata = dict(raw.get("metadata", {}) or {})
        actual_observed_at = str(raw.get("observed_at") or observed_at) if value is not None else None
        age_seconds = raw.get("age_seconds")
        if age_seconds is None and actual_observed_at:
            try:
                parsed = datetime.fromisoformat(actual_observed_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age_seconds = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
            except ValueError:
                age_seconds = None
        required_api = None
        if value is None:
            if feature_id in FeatureRegistry.KIS_REQUIRED:
                required_api = "KIS"
            elif feature_id in FeatureRegistry.OPEN_DART_REQUIRED:
                required_api = "OpenDART"
            elif feature_id in FeatureRegistry.ECOS_REQUIRED:
                required_api = "ECOS"
        if value is None:
            reason, resolution = unavailable_guidance(feature_id, str(definition.get("group") or ""), required_api)
            raw_metadata.update({"connection_reason": reason, "resolution": resolution})
            explanation = f"{reason} 해결 방법: {resolution}"
        source = str(raw.get("source") or ("실시간 계산" if value is not None else f"API 필요: {required_api}" if required_api else "미연결"))
        quality = str(raw.get("quality") or (("SIMULATED" if mode == "demo" else "LIVE") if value is not None else "API_KEY_REQUIRED" if required_api else "UNAVAILABLE"))
        source_type = (
            "API_REQUIRED" if value is None and required_api else
            "UNAVAILABLE" if value is None else
            "REALTIME" if mode in {"kis", "public"} and any(token in quality.upper() or token in source.upper() for token in ("LIVE", "REALTIME", "NAVER", "네이버")) else
            "PERIODIC" if mode == "public" else
            "SIMULATED" if mode == "demo" else
            "MANUAL"
        )
        quality_upper = quality.upper()
        frequency = str(raw_metadata.get("frequency") or "")
        stale_limit = (
            180.0 if any(token in quality_upper for token in ("LIVE", "REALTIME", "폴링")) else
            3600.0 if "YAHOO" in source.upper() or "5분" in frequency else
            172800.0 if "FRED" in source.upper() or "일" in frequency else
            4_000_000.0 if any(token in frequency for token in ("월", "분기", "연간")) else
            604800.0 if any(token in source.upper() for token in ("DART", "ECOS")) else
            2_592_000.0
        )
        freshness = "UNKNOWN" if value is None else str(raw.get("freshness") or "FRESH")
        if value is not None and isinstance(age_seconds, (int, float)) and age_seconds > stale_limit:
            freshness = "STALE"
            raw_metadata["stale"] = True
            raw_metadata["stale_limit_seconds"] = stale_limit
            if "STALE" not in quality_upper:
                quality = f"{quality} / STALE"
        direction = {
            "POSITIVE": "positive", "NEGATIVE": "negative", "CAUTION": "warning",
            "CONFLICTED": "warning", "UNKNOWN": "unknown",
        }.get(status, "neutral")
        return {
            "key": feature_id,
            "label": definition["display_name"],
            "feature_id": feature_id,
            "display_name": definition["display_name"],
            "group": definition["group"],
            "symbol": symbol,
            "value": value,
            "numeric_value": numeric,
            "previous_value": previous if isinstance(previous, (int, float)) else None,
            "delta": delta,
            "delta_percent": None if delta is None or not previous else delta / abs(float(previous)) * 100,
            "zscore_20": raw.get("zscore_20"),
            "zscore_60": raw.get("zscore_60"),
            "percentile_session": raw.get("percentile_session"),
            "trend": raw.get("trend", "UNKNOWN"),
            "status": status,
            "direction": direction,
            "polarity": definition["polarity"],
            "source": source,
            "source_type": source_type,
            "observed_at": actual_observed_at,
            "received_at": received_at,
            "age_seconds": age_seconds,
            "freshness": freshness,
            "reliability": float(raw.get("reliability", 0.0 if value is None else 0.65 if mode == "demo" else 0.9)),
            "description": definition.get("description", definition["display_name"]),
            "unit": definition.get("unit") or raw.get("unit") or "",
            "quality": quality,
            "explanation": explanation,
            "good_color": GOOD,
            "bad_color": BAD,
            "neutral_color": NEUTRAL,
            "caution_color": CAUTION,
            "metadata": {**raw_metadata, "required_api": required_api},
        }

    def valid_ids(self, features: list[dict[str, Any]]) -> set[str]:
        return {str(item.get("feature_id") or item.get("key")) for item in features}
