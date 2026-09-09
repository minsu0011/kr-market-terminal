from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from .ai_models import AiAnalysisResponse, strict_json_schema
from .analysis_engine import analyze_trade
from .config import Settings, provider_secret_verified
from .database import Database
from .feature_registry import FeatureRegistry
from .statistical_context import NullStatisticalContextProvider

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")
PROMPT_VERSION = "kr-daytrading-ai-v3-news-trade-plan-debrief"

SYSTEM_INSTRUCTIONS = """
당신은 한국 주식 단기 시장 분석 보조자다.
입력 JSON에 존재하는 데이터만 사용하고, 없는 시장 사실·뉴스·수급·공시·가격을 만들지 마라.
통계·백테스트 엔진이 미연결이면 승률, 평균손익, 기대수익, 표본 수를 추정하지 마라.
historical_statistics.available은 false로 두고 모든 수치 필드는 null로 반환하라.
금융 조언이나 확정적 예측이 아니라 조건부 분석만 제공하라.
“무조건 매수”, “확실한 상승”, “반드시 매도”라고 표현하지 마라.
회원사 창구 흐름과 기관 투자자 수급을 동일하게 취급하지 마라.
KOSPI·KOSDAQ·KOSPI200 선물·야간선물·베이시스·시장 폭을 먼저 확인하되, 장외·미제공 값을 추정하지 마라.
동종주 동반상승과 공격 강도는 가격과 거래량이 함께 확인됐는지 구분하고, 단일 동종주 움직임을 섹터 전체 사실로 확대하지 마라.
top_features에는 현재 판단을 실제로 바꾸는 시장·야간선물·동종주·호가 피처를 우선 검토하라.
뉴스 기사 수와 독립 원출처·사건 클러스터 수를 구분하라.
같은 통신사·보도자료·공시의 재배포를 독립 검증 여러 건으로 취급하지 마라.
뉴스 verification_status는 백엔드 규칙 결과를 그대로 사용하고 모델이 상향하지 마라.
뉴스 시점 이후의 price_volume_reaction만 해석하고 미래 반응을 만들지 마라.
긍정 뉴스만으로 BUY_REVIEW를 만들지 말고 가격·거래량·수급 중 최소 2개 그룹 확인을 요구하라.
뉴스 분석은 1) 사실 검증, 2) 중요도와 방향, 3) 가격·거래량 반응,
4) 외국인·기관 투자자·증권사 회원사 창구를 분리해서 설명하라.
기관 투자자 수급과 증권사 회원사 창구를 절대 합산하거나 같은 주체로 표현하지 마라.
상관관계를 인과관계로 단정하지 마라.
지연·결측 데이터는 중요도를 낮추고, 서로 충돌하는 피처는 양쪽을 설명하라.
top_features.feature_id와 feature_order, alerts_to_register.feature_id는 반드시 입력 features에 있는 ID만 사용하라.
top_features는 최대 12개다.
통계 엔진이 없으므로 STATISTICAL_ENGINE 임계값을 사용하지 마라.
AI_HEURISTIC 임계값은 explanation에 “AI 추정 경계값 · 백테스트 검증 전”을 포함하라.
conditional_trade_plan은 현재 입력 가격·호가·봉·피처로 검토 가능한 조건만 작성하라.
상승 조건은 LONG, 검증된 하락 조건은 SHORT로 분리하고 방향 근거가 부족하면 NONE을 사용하라.
진입 범위, 손절선, 1·2차 익절선은 원화 가격과 진입 기준 퍼센트를 함께 반환하되,
근거가 부족하면 null로 두고 NO_ENTRY를 사용하라. 허위 정밀도를 만들지 마라.
feature_triggers에는 피처가 현재 값에서 어느 방향으로 얼마만큼 움직여야 매수 검토가 되는지
입력 feature_id와 명시적 수치 경계로 작성하라. 통계치가 아니면 AI_HEURISTIC과
“AI 추정 경계값 · 백테스트 검증 전”을 반드시 표시하라.
가격 제안은 가상거래·수동 검토용이며 automatic_order는 항상 false다.
실제 주문 전송이나 주문 수량을 지시하지 말고 매수 검토·보유·관망·비중 축소 검토·매도 검토로만 표현하라.
trade_debrief_context.requested가 true이면 실제 저장 거래의 손익, 진입·청산 피처와
로컬 규칙 분석을 비교하여 성공한 이유와 실패한 이유, 진입·청산 평가, 다음 체크리스트를 분리하라.
거래가 없거나 복기가 요청되지 않았으면 trade_debrief.applicable은 false로 두라.
지정된 JSON Schema만 반환하라.
""".strip()


def _finite(value: Any, digits: int = 6) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        return round(number, digits) if math.isfinite(number) else None
    return value


def _candle_records(frame: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    records: list[dict[str, Any]] = []
    for row in frame.tail(limit).to_dict(orient="records"):
        records.append({
            "time": str(row.get("time")),
            "open": _finite(row.get("open"), 2),
            "high": _finite(row.get("high"), 2),
            "low": _finite(row.get("low"), 2),
            "close": _finite(row.get("close"), 2),
            "volume": int(float(row.get("volume") or 0)),
        })
    return records


def _resample(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    work["time"] = pd.to_datetime(work["time"], errors="coerce")
    work = work.dropna(subset=["time"]).set_index("time")
    result = work.resample(f"{minutes}min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna(subset=["close"]).reset_index()
    return result


class AnalysisInputBuilder:
    def __init__(self, registry: FeatureRegistry):
        self.registry = registry
        self.stats = NullStatisticalContextProvider()

    def build(
        self,
        *,
        analysis_type: str,
        symbol: str,
        question: str,
        frame: pd.DataFrame,
        daily_frame: pd.DataFrame,
        snapshot: dict[str, Any],
        tick: dict[str, Any],
        trades: list[dict[str, Any]],
        portfolio: dict[str, Any],
        trade_id: int | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(KST)
        opening = now.replace(hour=9, minute=0, second=0, microsecond=0)
        closing = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if now.weekday() >= 5:
            session_state = "CLOSED"
        elif now < opening:
            session_state = "PRE_OPEN"
        elif now <= closing:
            session_state = "OPEN"
        else:
            session_state = "CLOSED"

        latest = frame.iloc[-1] if frame is not None and not frame.empty else {}
        current = _finite(tick.get("price") or (latest.get("close") if hasattr(latest, "get") else None), 2)
        open_price = _finite(latest.get("open") if hasattr(latest, "get") else None, 2)
        high = _finite(frame["high"].max(), 2) if frame is not None and not frame.empty else None
        low = _finite(frame["low"].min(), 2) if frame is not None and not frame.empty else None
        volume_sum = float(frame["volume"].sum()) if frame is not None and not frame.empty else 0
        vwap = (
            _finite((frame["close"] * frame["volume"]).sum() / volume_sum, 2)
            if volume_sum > 0 else None
        )
        flat = snapshot.get("flat", {})
        profile = self.registry.profile(symbol)
        features = [self._compact_feature(item) for item in snapshot.get("features", [])]
        open_positions = [trade for trade in trades if trade.get("symbol") == symbol and trade.get("status") == "OPEN"]
        selected_trade = next((trade for trade in trades if trade_id and int(trade.get("id") or 0) == trade_id), None)

        def feature_context(ids: set[str]) -> dict[str, Any]:
            return {
                item["feature_id"]: item
                for item in features
                if item["feature_id"] in ids and item.get("value") is not None
            }

        return {
            "request_id": str(uuid.uuid4()),
            "requested_at": now.isoformat(),
            "analysis_type": analysis_type,
            "question": question.strip(),
            "symbol": {
                "code": symbol,
                "name": profile.get("name", symbol),
                "market": profile.get("market", "KOSPI"),
                "sector": profile.get("sector", ""),
                "industry": profile.get("industry", ""),
                "profile_id": profile.get("profile", "default"),
            },
            "timeframe": "1m",
            "market_session": {
                "state": session_state,
                "kst_time": now.isoformat(),
                "minutes_from_open": max(0, int((now - opening).total_seconds() / 60)),
                "minutes_to_close": max(0, int((closing - now).total_seconds() / 60)),
            },
            "price": {
                "current": current,
                "open": open_price,
                "high": high,
                "low": low,
                "previous_close": _finite(daily_frame.iloc[-2]["close"], 2) if len(daily_frame) >= 2 else None,
                "vwap": vwap,
                "return_1m": _finite(flat.get("ret_1")),
                "return_3m": _finite(flat.get("ret_3")),
                "return_5m": _finite(flat.get("ret_5")),
                "return_15m": self._return(frame, 15),
                "return_day": (
                    _finite((float(current) / float(open_price) - 1) * 100)
                    if current and open_price else None
                ),
            },
            "candles": {
                "one_minute_recent": _candle_records(frame, 120),
                "five_minute_recent": _candle_records(_resample(frame, 5), 72),
                "fifteen_minute_recent": _candle_records(_resample(frame, 15), 48),
                "daily_recent": _candle_records(daily_frame, 60),
            },
            "features": features,
            "investor_flow": feature_context({"foreign_net", "institution_net", "individual_net", "foreign_flow_acceleration"}),
            "member_flow": {"rows": snapshot.get("members", [])[:12], "note": "회원사 창구이며 기관 투자자 분류가 아님"},
            "orderbook": tick.get("orderbook") or {},
            "market_derivatives_context": {
                "features": feature_context({
                    "kospi_index", "kospi_change", "kosdaq_index", "kosdaq_change",
                    "kospi200_index", "kospi200_change", "kospi_breadth", "kosdaq_breadth",
                    "kospi_kosdaq_spread", "kospi200_futures_price",
                    "kospi200_futures_change", "futures_basis", "futures_open_interest",
                    "futures_open_interest_change", "futures_volume", "night_futures_change",
                    "night_futures_gap", "krx_derivatives_session", "program_trading",
                    "program_arbitrage_net", "program_non_arbitrage_net", "put_call_ratio",
                    "option_implied_volatility", "vkospi", "usdkrw_change",
                }),
                "night_value_must_not_be_inferred": True,
            },
            "peer_context": {
                "rows": snapshot.get("peer_context", [])[:12],
                "features": feature_context({
                    "peer_observed_count", "peer_up_ratio", "peer_equal_weight_return",
                    "peer_joint_rise_count", "peer_joint_fall_count", "peer_attack_score",
                    "peer_relative_strength", "kospi_relative_strength",
                    "peer_movement_keyword",
                }),
                "attention_keywords": snapshot.get("attention_keywords", []),
                "attack_requires_price_and_volume": True,
            },
            "microstructure_context": feature_context({
                "spread_bps", "orderbook_imbalance", "microprice_gap_bps",
                "top5_depth_quantity", "vwap_distance", "relative_volume_1m",
                "day_range_position", "price_acceleration_1m", "opening_range_breakout",
            }),
            "market_breadth": feature_context({"decline_ratio", "crash_ratio", "top50_contagion"}),
            "sector_context": {"profile": profile, "features": feature_context(set(self.registry.profile_feature_ids.get(profile.get("profile", ""), [])))},
            "global_context": feature_context({"sp500", "nasdaq", "nasdaq_return", "soxx_return", "vix"}),
            "commodity_context": feature_context({"oil", "wti", "brent", "gold", "copper_price", "lithium_price"}),
            "rate_fx_context": feature_context({"usdkrw", "eurkrw", "us2y", "us10y", "bok_base_rate"}),
            "news_context": {
                **snapshot.get("news_context", {}),
                "feature_context": feature_context({
                    "news_verification_status", "unique_news_event_count", "raw_article_count",
                    "independent_source_count", "primary_source_count", "syndicated_copy_count",
                    "news_materiality_max", "news_price_confirmation", "news_volume_confirmation",
                    "news_institution_confirmation", "news_foreign_confirmation",
                    "news_member_flow_confirmation", "news_data_quality_score",
                }),
            },
            "dart_context": feature_context({item["feature_id"] for item in features if item["feature_id"].startswith("dart_")}),
            "position": {
                "open_positions": [
                    {
                        "trade_id": trade["id"],
                        "entry_time": trade["entry_time"],
                        "entry_price": trade["entry_price"],
                        "quantity": trade["entry_quantity"],
                    }
                    for trade in open_positions
                ],
                "portfolio": portfolio,
            },
            "trade_debrief_context": self._trade_debrief_context(selected_trade, trade_id),
            "data_quality": self._data_quality(features, snapshot.get("mode", "demo")),
            "statistical_context": {
                "available": False,
                "reason": "통계·백테스트 엔진 미연결",
            },
        }

    @staticmethod
    def _trade_debrief_context(trade: dict[str, Any] | None, requested_trade_id: int | None) -> dict[str, Any]:
        if requested_trade_id is None:
            return {"requested": False, "trade": None, "local_rule_analysis": None}
        if not trade:
            return {
                "requested": True,
                "trade": None,
                "local_rule_analysis": None,
                "warning": "요청한 거래를 찾을 수 없음",
            }
        keys = {
            "price", "ret_1", "ret_3", "ret_5", "ret_20", "ema_gap", "rsi14",
            "volume_z", "relative_volume_1m", "vwap_distance", "orderbook_imbalance",
            "foreign_net", "institution_net", "individual_net", "flow_acceleration",
            "decline_ratio", "vix", "short_ratio", "negative_news",
            "news_verification_status", "news_price_confirmation",
            "news_volume_confirmation", "news_institution_confirmation",
            "news_foreign_confirmation", "news_member_flow_confirmation",
            "top_member_name", "top_member_net", "exit_member_name", "exit_member_net",
        }

        def compact(values: dict[str, Any] | None) -> dict[str, Any]:
            return {
                key: _finite(value)
                for key, value in (values or {}).items()
                if key in keys and isinstance(value, (bool, int, float, str))
            }

        entry_features = compact(trade.get("entry_features"))
        exit_features = compact(trade.get("exit_features"))
        local = None
        if trade.get("status") == "CLOSED":
            local = analyze_trade(
                entry_features,
                exit_features,
                float(trade.get("net_pnl") or 0),
                float(trade.get("return_pct") or 0),
                trade.get("side") or "LONG",
            )
        return {
            "requested": True,
            "trade": {
                "id": trade.get("id"),
                "symbol": trade.get("symbol"),
                "symbol_name": trade.get("symbol_name"),
                "status": trade.get("status"),
                "side": trade.get("side") or "LONG",
                "entry_time": trade.get("entry_time"),
                "entry_price_krw": _finite(trade.get("entry_price"), 2),
                "entry_quantity": trade.get("entry_quantity"),
                "exit_time": trade.get("exit_time"),
                "exit_price_krw": _finite(trade.get("exit_price"), 2),
                "net_pnl_krw": _finite(trade.get("net_pnl"), 2),
                "return_pct": _finite(trade.get("return_pct"), 6),
                "entry_features": entry_features,
                "exit_features": exit_features,
            },
            "local_rule_analysis": local,
        }

    @staticmethod
    def _return(frame: pd.DataFrame, periods: int) -> float | None:
        if frame is None or len(frame) <= periods:
            return None
        start = float(frame.iloc[-periods - 1]["close"])
        end = float(frame.iloc[-1]["close"])
        return _finite((end / start - 1) * 100)

    @staticmethod
    def _compact_feature(item: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "feature_id", "display_name", "group", "value", "numeric_value",
            "previous_value", "delta", "delta_percent", "zscore_20", "zscore_60",
            "percentile_session", "trend", "status", "polarity", "source",
            "source_type", "observed_at", "age_seconds", "freshness", "reliability",
            "description", "unit", "explanation",
        }
        return {key: _finite(value) for key, value in item.items() if key in allowed}

    @staticmethod
    def _data_quality(features: list[dict[str, Any]], mode: str) -> dict[str, Any]:
        total = max(len(features), 1)
        missing = [item["feature_id"] for item in features if item.get("value") is None]
        stale = [item["feature_id"] for item in features if item.get("freshness") == "STALE"]
        available_ratio = (total - len(missing)) / total
        score = round(available_ratio * (70 if mode == "demo" else 100))
        warnings = ["DEMO 데이터는 실제 시세가 아닙니다."] if mode == "demo" else []
        return {
            "score": score,
            "mode": mode,
            "missing_feature_ids": missing[:150],
            "stale_feature_ids": stale[:150],
            "warnings": warnings,
        }


class AiAnalysisService:
    def __init__(self, settings: Settings, db: Database, registry: FeatureRegistry):
        self.settings = settings
        self.db = db
        self.registry = registry

    @staticmethod
    def request_hash(payload: dict[str, Any]) -> str:
        canonical = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
        canonical.pop("request_id", None)
        canonical.pop("requested_at", None)
        canonical.get("market_session", {}).pop("kst_time", None)
        for feature in canonical.get("features", []):
            feature.pop("received_at", None)
            feature.pop("age_seconds", None)
        raw = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def execute(
        self,
        payload: dict[str, Any],
        *,
        cancel: threading.Event,
        progress: Callable[[str, int], None],
    ) -> dict[str, Any]:
        if not self.settings.openai_analysis_enabled:
            raise RuntimeError("GPT 분석이 서버 설정에서 꺼져 있습니다. OPENAI_ANALYSIS_ENABLED=true로 설정하십시오.")
        request_hash = self.request_hash(payload)
        cached = self.db.find_cached_ai_analysis(request_hash, datetime.now(UTC).isoformat())
        if cached and cached.get("response_json"):
            progress("동일 데이터의 유효한 저장 분석을 재사용했습니다.", 100)
            return {"analysis": cached["response_json"], "cached": True, "record": cached}
        if cancel.is_set():
            raise asyncio.CancelledError()
        progress("분석 입력을 정규화했습니다.", 15)

        if self.settings.openai_fixture_mode:
            progress("키 없는 검증용 fixture 응답을 읽는 중입니다.", 55)
            raw = self.fixture_response(payload)
        else:
            if not self.settings.openai_api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY가 없습니다. 화면 위쪽의 '🔑 API'를 눌러 "
                    "OpenAI API 키를 입력하고 저장·연결 테스트를 실행하십시오."
                )
            if not provider_secret_verified("openai", "OPENAI_API_KEY"):
                raise RuntimeError(
                    "저장된 OpenAI API 키가 아직 실제 연결 테스트를 통과하지 않았습니다. "
                    "화면 위쪽의 '🔑 API'에서 연결 테스트 후 다시 실행하십시오."
                )
            progress("GPT-5.6 Sol 최고 추론 모델이 분석 중입니다.", 35)
            raw = self._openai_request(payload, cancel, progress)

        progress("응답 스키마와 피처 ID를 검증 중입니다.", 85)
        sanitized = self.sanitize(raw, payload)
        record = self.db.save_ai_analysis(
            payload,
            sanitized,
            request_hash=request_hash,
            model=self.settings.openai_model,
            prompt_version=PROMPT_VERSION,
        )
        progress("분석을 SQLite에 저장했습니다.", 100)
        return {"analysis": sanitized, "cached": False, "record": record}

    def _openai_request(
        self,
        payload: dict[str, Any],
        cancel: threading.Event,
        progress: Callable[[str, int], None],
    ) -> dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI 공식 SDK가 설치되지 않았습니다. RUN.bat를 다시 실행하십시오.") from exc

        client = OpenAI(
            api_key=self.settings.openai_api_key,
            timeout=self.settings.openai_timeout_seconds,
            max_retries=self.settings.openai_max_retries,
        )
        common = {
            "model": self.settings.openai_model,
            "instructions": SYSTEM_INSTRUCTIONS,
            "input": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "reasoning": {"effort": "max"},
            "store": self.settings.openai_store_responses,
        }
        try:
            if self.settings.openai_stream:
                stream = client.responses.create(
                    **common,
                    stream=True,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "kr_market_analysis",
                            "strict": True,
                            "schema": strict_json_schema(AiAnalysisResponse),
                        },
                        "verbosity": "medium",
                    },
                )
                chunks: list[str] = []
                refusal: list[str] = []
                for event in stream:
                    if cancel.is_set():
                        close = getattr(stream, "close", None)
                        if callable(close):
                            close()
                        raise asyncio.CancelledError()
                    event_type = getattr(event, "type", "")
                    if event_type == "response.output_text.delta":
                        chunks.append(str(getattr(event, "delta", "")))
                        progress("구조화 응답을 스트리밍으로 수신 중입니다.", min(80, 45 + len(chunks) // 4))
                    elif event_type == "response.refusal.delta":
                        refusal.append(str(getattr(event, "delta", "")))
                if refusal:
                    raise RuntimeError(f"모델이 분석 요청을 거부했습니다: {''.join(refusal)[:200]}")
                if not chunks:
                    raise RuntimeError("OpenAI가 분석 본문을 반환하지 않았습니다.")
                return json.loads("".join(chunks))

            response = client.responses.parse(
                **common,
                text_format=AiAnalysisResponse,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise RuntimeError("모델 거부 또는 빈 구조화 응답입니다.")
            return parsed.model_dump(mode="json")
        except asyncio.CancelledError:
            raise
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI 구조화 응답을 JSON으로 읽지 못했습니다.") from exc
        except Exception as exc:
            name = type(exc).__name__.lower()
            message = str(exc)
            if "authentication" in name or "401" in message:
                raise RuntimeError("OpenAI API 키가 올바르지 않거나 권한이 없습니다. '🔑 API'에서 키를 다시 저장하고 연결 테스트하십시오.") from exc
            if "timeout" in name or "timed out" in message.lower():
                raise RuntimeError(f"OpenAI 분석이 {self.settings.openai_timeout_seconds:g}초 안에 끝나지 않았습니다.") from exc
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"OpenAI 분석 실패: {type(exc).__name__}") from exc

    def sanitize(self, raw: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        parsed = AiAnalysisResponse.model_validate(raw).model_dump(mode="json")
        feature_map = {item["feature_id"]: item for item in payload.get("features", [])}
        valid_ids = set(feature_map)
        seen: set[str] = set()
        top: list[dict[str, Any]] = []
        for feature in sorted(parsed["top_features"], key=lambda item: item["rank"]):
            feature_id = feature["feature_id"]
            if feature_id not in valid_ids or feature_id in seen:
                logger.warning("GPT가 반환한 잘못된 feature_id 제거: %s", feature_id)
                continue
            seen.add(feature_id)
            current = feature_map[feature_id]
            feature["display_name"] = current.get("display_name", feature_id)
            feature["group"] = current.get("group", "")
            feature["current_value"] = current.get("value")
            feature["current_display"] = self.display_value(current)
            for threshold in feature["thresholds"].values():
                if threshold["basis"] == "STATISTICAL_ENGINE":
                    threshold.update({
                        "operator": "NONE",
                        "value1": None,
                        "value2": None,
                        "basis": "UNAVAILABLE",
                        "confidence": 0.0,
                        "explanation": "통계·백테스트 엔진 미연결",
                    })
                elif threshold["basis"] == "AI_HEURISTIC" and "백테스트 검증 전" not in threshold["explanation"]:
                    threshold["explanation"] = f"AI 추정 경계값 · 백테스트 검증 전 · {threshold['explanation']}"
            top.append(feature)
            if len(top) == 12:
                break
        for rank, feature in enumerate(top, start=1):
            feature["rank"] = rank
        parsed["top_features"] = top
        parsed["feature_order"] = [
            feature_id for feature_id in dict.fromkeys(parsed["feature_order"])
            if feature_id in valid_ids
        ]
        if not parsed["feature_order"]:
            parsed["feature_order"] = [feature["feature_id"] for feature in top]
        parsed["alerts_to_register"] = [
            alert for alert in parsed["alerts_to_register"]
            if alert["feature_id"] in valid_ids
            and alert["operator"] != "NONE"
            and alert["threshold"] is not None
            and alert["basis"] != "STATISTICAL_ENGINE"
        ]
        plan = parsed["conditional_trade_plan"]
        plan["automatic_order"] = False
        plan["data_mode"] = str(payload.get("data_quality", {}).get("mode") or "unknown").upper()
        reference = payload.get("price", {}).get("current")
        reference = float(reference) if isinstance(reference, (int, float)) and reference > 0 else None
        plan["reference_price_krw"] = round(reference, 2) if reference else None
        plan["feature_triggers"] = [
            trigger for trigger in plan.get("feature_triggers", [])
            if trigger.get("feature_id") in valid_ids
            and trigger.get("buy_operator") != "NONE"
            and trigger.get("buy_threshold") is not None
        ][:8]
        for trigger in plan["feature_triggers"]:
            current = feature_map[trigger["feature_id"]]
            trigger["display_name"] = current.get("display_name", trigger["feature_id"])
            trigger["current_display"] = self.display_value(current)
            if trigger.get("threshold_basis") == "STATISTICAL_ENGINE":
                trigger.update({
                    "buy_operator": "NONE",
                    "buy_threshold": None,
                    "buy_threshold2": None,
                    "threshold_basis": "UNAVAILABLE",
                    "required_move": "통계·백테스트 엔진 미연결",
                    "explanation": "통계·백테스트 엔진 미연결",
                })
            elif trigger.get("threshold_basis") == "AI_HEURISTIC" and "백테스트 검증 전" not in trigger.get("explanation", ""):
                trigger["explanation"] = f"AI 추정 경계값 · 백테스트 검증 전 · {trigger.get('explanation', '')}".strip(" ·")
        plan["feature_triggers"] = [
            trigger for trigger in plan["feature_triggers"]
            if trigger["buy_operator"] != "NONE" and trigger["buy_threshold"] is not None
        ]

        def plausible_price(value: Any) -> float | None:
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
                return None
            price = float(value)
            if reference and not (reference * 0.65 <= price <= reference * 1.35):
                return None
            return round(price, 2)

        for key in (
            "entry_price_low_krw", "entry_price_high_krw", "stop_loss_price_krw",
            "take_profit_1_price_krw", "take_profit_2_price_krw",
        ):
            plan[key] = plausible_price(plan.get(key))
        if plan["entry_price_low_krw"] and plan["entry_price_high_krw"] and plan["entry_price_low_krw"] > plan["entry_price_high_krw"]:
            plan["entry_price_low_krw"], plan["entry_price_high_krw"] = plan["entry_price_high_krw"], plan["entry_price_low_krw"]
        entry_basis = None
        if plan["entry_price_low_krw"] and plan["entry_price_high_krw"]:
            entry_basis = (plan["entry_price_low_krw"] + plan["entry_price_high_krw"]) / 2
        elif plan["entry_price_low_krw"] or plan["entry_price_high_krw"]:
            entry_basis = plan["entry_price_low_krw"] or plan["entry_price_high_krw"]
        elif reference:
            entry_basis = reference
        side = plan.get("side") or "NONE"
        stop_is_valid = bool(
            entry_basis and plan["stop_loss_price_krw"]
            and (
                side == "LONG" and plan["stop_loss_price_krw"] < entry_basis
                or side == "SHORT" and plan["stop_loss_price_krw"] > entry_basis
            )
        )
        if stop_is_valid:
            plan["stop_loss_pct"] = round(
                (
                    plan["stop_loss_price_krw"] / entry_basis - 1
                    if side == "LONG"
                    else 1 - plan["stop_loss_price_krw"] / entry_basis
                ) * 100,
                3,
            )
        else:
            plan["stop_loss_price_krw"] = None
            plan["stop_loss_pct"] = None
        for price_key, pct_key in (
            ("take_profit_1_price_krw", "take_profit_1_pct"),
            ("take_profit_2_price_krw", "take_profit_2_pct"),
        ):
            target_is_valid = bool(
                entry_basis and plan[price_key]
                and (
                    side == "LONG" and plan[price_key] > entry_basis
                    or side == "SHORT" and plan[price_key] < entry_basis
                )
            )
            if target_is_valid:
                plan[pct_key] = round(
                    (
                        plan[price_key] / entry_basis - 1
                        if side == "LONG"
                        else 1 - plan[price_key] / entry_basis
                    ) * 100,
                    3,
                )
            else:
                plan[price_key] = None
                plan[pct_key] = None
        if plan.get("trailing_stop_pct") is not None:
            trailing = abs(float(plan["trailing_stop_pct"]))
            plan["trailing_stop_pct"] = round(trailing, 3) if 0 < trailing <= 15 else None
        if plan["entry_method"] == "NO_ENTRY":
            plan["applicable"] = False
            plan["side"] = "NONE"
        if plan["data_mode"] not in {"KIS", "PUBLIC"}:
            plan["risk_notice"] = (
                "DEMO/비실시간 데이터에서는 가격 계획을 실행하지 마십시오. 공개 실데이터 또는 KIS 시세 재확인 필요. "
                + plan.get("risk_notice", "")
            ).strip()
        elif plan["data_mode"] == "PUBLIC":
            plan["risk_notice"] = (
                "무키 공개 피드는 데이터별 지연 가능성이 있으므로 화면의 출처·품질과 현재 호가를 재확인하십시오. "
                + plan.get("risk_notice", "")
            ).strip()

        requested_trade = payload.get("trade_debrief_context", {})
        debrief = parsed["trade_debrief"]
        expected_trade = requested_trade.get("trade") or {}
        debrief["trade_id"] = expected_trade.get("id")
        debrief["applicable"] = bool(requested_trade.get("requested") and expected_trade)
        if not requested_trade.get("requested"):
            debrief.update({
                "applicable": False,
                "trade_id": None,
                "outcome": "NOT_REQUESTED",
                "execution_summary": "거래 복기가 요청되지 않았습니다.",
                "what_worked": [],
                "what_failed": [],
                "entry_assessment": "",
                "exit_assessment": "",
                "next_time_checklist": [],
            })

        # No model output can claim unvalidated historical performance.
        parsed["historical_statistics"] = {
            "available": False,
            "sample_count": None,
            "win_rate": None,
            "expected_return_5m": None,
            "expected_return_15m": None,
            "expected_return_30m": None,
            "average_net_return": None,
            "lower_decile_return": None,
            "message": "통계·백테스트 엔진 미연결",
        }
        now = datetime.now(UTC)
        parsed["analysis_id"] = str(uuid.uuid4())
        parsed["created_at"] = now.isoformat()
        parsed["expires_at"] = (now + timedelta(minutes=15)).isoformat()
        return parsed

    @staticmethod
    def display_value(feature: dict[str, Any]) -> str:
        value = feature.get("value")
        unit = feature.get("unit") or ""
        if value is None:
            return "미연결"
        if isinstance(value, (int, float)):
            formatted = f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.3f}".rstrip("0").rstrip(".")
            return f"₩{formatted}" if unit == "원" else f"{formatted}{unit}"
        return f"{value}{unit}"

    def fixture_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        available = [item for item in payload.get("features", []) if item.get("value") is not None]
        preferred = [
            "night_futures_change", "kospi200_futures_change", "kospi_change",
            "kosdaq_change", "futures_basis", "peer_attack_score",
            "peer_movement_keyword", "orderbook_imbalance", "vwap_distance",
            "relative_volume_1m", "foreign_net", "institution_net", "ret_5",
        ]
        ordered = sorted(available, key=lambda item: preferred.index(item["feature_id"]) if item["feature_id"] in preferred else 999)
        picked = ordered[: min(6, len(ordered))]

        def threshold(operator: str = "NONE", value: float | None = None, severity: str = "UNAVAILABLE") -> dict[str, Any]:
            if severity == "AI_HEURISTIC":
                explanation = "AI 추정 경계값 · 백테스트 검증 전 · 현재 값 주변의 관찰 경계"
            else:
                explanation = "통계·백테스트 엔진 미연결"
            return {
                "operator": operator,
                "value1": value,
                "value2": None,
                "basis": severity,
                "confidence": 0.45 if severity == "AI_HEURISTIC" else 0.0,
                "explanation": explanation,
            }

        top = []
        alerts = []
        for rank, item in enumerate(picked, start=1):
            numeric = item.get("numeric_value")
            direction = item.get("status") if item.get("status") in {"POSITIVE", "NEGATIVE", "NEUTRAL", "CONFLICTED", "UNKNOWN"} else "UNKNOWN"
            danger = threshold()
            if isinstance(numeric, (int, float)):
                operator = "LTE" if direction == "POSITIVE" else "GTE"
                danger = threshold(operator, round(float(numeric) * (0.9 if operator == "LTE" else 1.1), 6), "AI_HEURISTIC")
                alerts.append({
                    "feature_id": item["feature_id"],
                    "operator": operator,
                    "threshold": danger["value1"],
                    "second_threshold": None,
                    "severity": "DANGER",
                    "basis": "AI_HEURISTIC",
                    "explanation": danger["explanation"],
                })
            top.append({
                "feature_id": item["feature_id"],
                "display_name": item["display_name"],
                "group": item["group"],
                "rank": rank,
                "importance": max(40, 96 - rank * 8),
                "current_value": item.get("value"),
                "current_display": self.display_value(item),
                "direction_now": direction,
                "reason": f"{item['display_name']}의 현재 의미 방향과 데이터 신선도를 우선 확인합니다.",
                "supporting_data": [f"상태 {direction}", f"출처 {item.get('source', '-')}"],
                "thresholds": {
                    "positive": threshold(),
                    "caution": threshold(),
                    "danger": danger,
                    "invalidation": threshold(),
                },
            })
        now = datetime.now(UTC)
        quality = payload.get("data_quality", {})
        question = payload.get("question") or "현재 피처의 조건부 상태를 분석해 주세요."
        return {
            "analysis_id": str(uuid.uuid4()),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=15)).isoformat(),
            "market_regime": {"label": "DEMO 조건부 관찰", "confidence": 0.54, "summary": "fixture 기반 UI 검증이며 실제 OpenAI 응답이 아닙니다."},
            "symbol_state": {"label": "피처 혼합", "confidence": 0.58, "summary": "연결된 실시간·최근 피처 사이의 의미 방향을 비교했습니다."},
            "overall_action": {"code": "WAIT", "korean_label": "관망", "confidence": 0.56, "reason": "검증된 통계 엔진이 없으므로 조건 확인이 우선입니다."},
            "data_quality": {
                "score": int(quality.get("score", 0)),
                "stale_sources": list(quality.get("stale_feature_ids", []))[:10],
                "missing_sources": list(quality.get("missing_feature_ids", []))[:20],
                "warnings": list(quality.get("warnings", [])) + ["OpenAI 키 미사용 fixture"],
            },
            "historical_statistics": {
                "available": False, "sample_count": None, "win_rate": None,
                "expected_return_5m": None, "expected_return_15m": None,
                "expected_return_30m": None, "average_net_return": None,
                "lower_decile_return": None, "message": "통계·백테스트 엔진 미연결",
            },
            "top_features": top,
            "buy_review_conditions": ["긍정 피처가 유지되고 위험 피처가 완화되는지 확인"],
            "hold_conditions": ["보유 포지션이 있다면 판단 무효화 조건 전까지 조건을 재확인"],
            "caution_conditions": ["미연결·지연 데이터 비중이 높으면 신뢰도를 낮춤"],
            "reduce_review_conditions": ["위험 경계가 동시에 여러 개 충족될 때 비중 축소 검토"],
            "sell_review_conditions": ["추세와 수급이 함께 부정으로 전환될 때 매도 검토"],
            "invalidation_conditions": ["분석 만료 또는 핵심 피처 방향 급변"],
            "feature_order": [item["feature_id"] for item in top],
            "alerts_to_register": alerts[:4],
            "conditional_trade_plan": {
                "applicable": False,
                "data_mode": str(payload.get("data_quality", {}).get("mode") or "unknown").upper(),
                "automatic_order": False,
                "plan_label": "조건 확인 전 진입 없음",
                "side": "NONE",
                "reference_price_krw": payload.get("price", {}).get("current"),
                "entry_method": "NO_ENTRY",
                "entry_price_low_krw": None,
                "entry_price_high_krw": None,
                "entry_instruction": "실제 KIS 시세와 매수 검토 조건이 확인될 때까지 주문 가격을 만들지 않습니다.",
                "stop_loss_price_krw": None,
                "stop_loss_pct": None,
                "take_profit_1_price_krw": None,
                "take_profit_1_pct": None,
                "take_profit_2_price_krw": None,
                "take_profit_2_pct": None,
                "trailing_stop_pct": None,
                "feature_triggers": [
                    {
                        "feature_id": item["feature_id"],
                        "display_name": item["display_name"],
                        "current_display": self.display_value(item),
                        "buy_operator": "GTE",
                        "buy_threshold": round(float(item["numeric_value"]) * 1.05, 6),
                        "buy_threshold2": None,
                        "threshold_basis": "AI_HEURISTIC",
                        "required_move": "현재 값 대비 약 5% 개선 여부 확인",
                        "explanation": "AI 추정 경계값 · 백테스트 검증 전 · fixture 화면 검증용 조건",
                    }
                    for item in picked[:3] if isinstance(item.get("numeric_value"), (int, float))
                ],
                "no_trade_conditions": ["DEMO 데이터", "실시간 가격·수급 미확인"],
                "risk_notice": "가상거래·수동 검토 전용이며 자동주문은 없습니다.",
            },
            "trade_debrief": self._fixture_trade_debrief(payload),
            "limitations": ["실제 OpenAI 호출 미검증", "역사 통계·백테스트 엔진 미연결"],
            "human_readable_summary": f"질문: {question} 연결된 피처만으로 조건부 상태를 정리했습니다. 자동 주문은 실행하지 않습니다.",
        }

    @staticmethod
    def _fixture_trade_debrief(payload: dict[str, Any]) -> dict[str, Any]:
        context = payload.get("trade_debrief_context", {})
        trade = context.get("trade") or {}
        if not context.get("requested") or not trade:
            return {
                "applicable": False,
                "trade_id": None,
                "outcome": "NOT_REQUESTED",
                "execution_summary": "거래 복기가 요청되지 않았습니다.",
                "what_worked": [],
                "what_failed": [],
                "entry_assessment": "",
                "exit_assessment": "",
                "next_time_checklist": [],
            }
        net_pnl = float(trade.get("net_pnl_krw") or 0)
        outcome = "OPEN" if trade.get("status") != "CLOSED" else "WIN" if net_pnl > 0 else "LOSS" if net_pnl < 0 else "BREAKEVEN"
        local = context.get("local_rule_analysis") or {}
        reasons = [item.get("detail", "") for item in local.get("reasons", []) if item.get("detail")]
        return {
            "applicable": True,
            "trade_id": trade.get("id"),
            "outcome": outcome,
            "execution_summary": local.get("summary") or "저장된 진입·청산 피처를 비교했습니다.",
            "what_worked": reasons[:3] if outcome == "WIN" else [],
            "what_failed": reasons[:3] if outcome in {"LOSS", "BREAKEVEN"} else [],
            "entry_assessment": "진입 당시 저장된 추세·거래량·수급 조건을 확인했습니다.",
            "exit_assessment": "청산 당시 저장된 피처와 실제 손익을 비교했습니다.",
            "next_time_checklist": ["진입 전 추세·거래량·수급 독립 확인", "손절·익절선을 진입 전에 기록"],
        }


class AiJobManager:
    def __init__(self, service: AiAnalysisService):
        self.service = service
        self.jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def start(self, payload: dict[str, Any]) -> str:
        job_id = str(uuid.uuid4())
        cancel = threading.Event()
        with self._lock:
            self.jobs[job_id] = {
                "job_id": job_id,
                "status": "QUEUED",
                "message": "분석 대기 중",
                "progress": 0,
                "created_at": datetime.now(UTC).isoformat(),
                "result": None,
                "error": None,
                "_cancel": cancel,
            }
        asyncio.create_task(self._run(job_id, payload, cancel))
        return job_id

    async def _run(self, job_id: str, payload: dict[str, Any], cancel: threading.Event) -> None:
        self._update(job_id, status="RUNNING", message="분석 작업 시작", progress=5)

        def progress(message: str, percent: int) -> None:
            self._update(job_id, message=message, progress=percent)

        try:
            result = await asyncio.to_thread(self.service.execute, payload, cancel=cancel, progress=progress)
            if cancel.is_set():
                self._update(job_id, status="CANCELLED", message="사용자가 분석을 취소했습니다.", progress=100)
            else:
                self._update(job_id, status="COMPLETED", message="분석 완료", progress=100, result=result)
        except asyncio.CancelledError:
            self._update(job_id, status="CANCELLED", message="사용자가 분석을 취소했습니다.", progress=100)
        except Exception as exc:
            logger.warning("GPT 분석 작업 실패: %s", exc)
            self._update(job_id, status="FAILED", message=str(exc), progress=100, error=str(exc))

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            if job_id in self.jobs:
                self.jobs[job_id].update(changes)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            return {key: value for key, value in job.items() if not key.startswith("_")}

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job or job["status"] not in {"QUEUED", "RUNNING"}:
                return False
            job["_cancel"].set()
            job.update({"status": "CANCELLING", "message": "취소 요청 처리 중"})
            return True


class AlertEvaluator:
    def __init__(self, db: Database):
        self.db = db

    def apply_from_analysis(self, analysis_id: str, symbol: str) -> list[dict[str, Any]]:
        record = self.db.get_ai_analysis(analysis_id)
        if not record or not record.get("response_json"):
            raise ValueError("분석 기록을 찾을 수 없습니다.")
        response = record["response_json"]
        if record["symbol"] != symbol:
            raise ValueError("분석 종목과 경고선 종목이 다릅니다.")
        alerts = []
        for proposal in response.get("alerts_to_register", []):
            alerts.append({
                "alert_id": str(uuid.uuid4()),
                "analysis_id": analysis_id,
                "symbol": symbol,
                "feature_id": proposal["feature_id"],
                "operator": proposal["operator"],
                "threshold": proposal.get("threshold"),
                "second_threshold": proposal.get("second_threshold"),
                "severity": proposal["severity"],
                "basis": proposal["basis"],
                "explanation": proposal.get("explanation", ""),
                "expires_at": response["expires_at"],
            })
        plan = response.get("conditional_trade_plan") or {}
        plan_side = plan.get("side") or "NONE"
        for trigger in plan.get("feature_triggers", []):
            if trigger.get("buy_operator") == "NONE" or trigger.get("buy_threshold") is None:
                continue
            alerts.append({
                "alert_id": str(uuid.uuid4()),
                "analysis_id": analysis_id,
                "symbol": symbol,
                "feature_id": trigger["feature_id"],
                "operator": trigger["buy_operator"],
                "threshold": trigger.get("buy_threshold"),
                "second_threshold": trigger.get("buy_threshold2"),
                "severity": "POSITIVE" if plan_side == "LONG" else "DANGER" if plan_side == "SHORT" else "CAUTION",
                "basis": trigger.get("threshold_basis") or "AI_HEURISTIC",
                "explanation": f"[{plan_side}_ENTRY_REVIEW] {trigger.get('required_move', '')} · {trigger.get('explanation', '')}",
                "expires_at": response["expires_at"],
            })
        for feature in response.get("top_features", []):
            for threshold_name, severity in (("danger", "DANGER"), ("invalidation", "INVALIDATION")):
                threshold = (feature.get("thresholds") or {}).get(threshold_name) or {}
                if threshold.get("operator") == "NONE" or threshold.get("value1") is None:
                    continue
                alerts.append({
                    "alert_id": str(uuid.uuid4()),
                    "analysis_id": analysis_id,
                    "symbol": symbol,
                    "feature_id": feature["feature_id"],
                    "operator": threshold["operator"],
                    "threshold": threshold.get("value1"),
                    "second_threshold": threshold.get("value2"),
                    "severity": severity,
                    "basis": threshold.get("basis") or "AI_HEURISTIC",
                    "explanation": f"[EXIT_REVIEW] {threshold.get('explanation', '')}",
                    "expires_at": response["expires_at"],
                })
        return self.db.create_ai_alerts(alerts)

    def evaluate(self, symbol: str, flat: dict[str, Any]) -> list[dict[str, Any]]:
        now = datetime.now(UTC).isoformat()
        self.db.expire_ai_alerts(now)
        triggered: list[dict[str, Any]] = []
        for alert in self.db.list_ai_alerts(symbol, active_only=True):
            value = flat.get(alert["feature_id"])
            if not isinstance(value, (int, float)):
                continue
            threshold = alert.get("threshold")
            second = alert.get("second_threshold")
            operator = alert["operator"]
            matched = (
                operator == "GT" and value > threshold or
                operator == "GTE" and value >= threshold or
                operator == "LT" and value < threshold or
                operator == "LTE" and value <= threshold or
                operator == "BETWEEN" and threshold <= value <= second
            ) if threshold is not None else False
            if matched and self.db.mark_ai_alert_triggered(alert["alert_id"], now):
                triggered.append({**alert, "current_value": value, "triggered_at": now})
        return triggered
