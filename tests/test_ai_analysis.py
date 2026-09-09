from __future__ import annotations

import copy
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from market_terminal.ai_analysis import AiAnalysisService, AlertEvaluator
from market_terminal.config import Settings
from market_terminal.database import Database
from market_terminal.feature_registry import FeaturePolarityResolver, FeatureRegistry


ROOT = Path(__file__).resolve().parents[1]


def input_payload() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "request_id": "request-fixture",
        "requested_at": now,
        "analysis_type": "SYMBOL",
        "question": "현재 중요한 피처는?",
        "symbol": {
            "code": "005930", "name": "삼성전자", "market": "KOSPI",
            "sector": "반도체", "industry": "메모리·종합전자",
            "profile_id": "semiconductor_memory",
        },
        "timeframe": "1m",
        "market_session": {"state": "OPEN", "kst_time": now, "minutes_from_open": 80, "minutes_to_close": 310},
        "price": {"current": 82000, "open": 81500, "high": 82300, "low": 81200, "previous_close": 81400, "vwap": 81850, "return_1m": 0.1, "return_3m": 0.2, "return_5m": 0.3, "return_15m": 0.4, "return_day": 0.61},
        "candles": {"one_minute_recent": [], "five_minute_recent": [], "fifteen_minute_recent": [], "daily_recent": []},
        "features": [
            {
                "feature_id": "ret_5", "display_name": "5분 수익률", "group": "가격·추세",
                "value": 0.3, "numeric_value": 0.3, "status": "POSITIVE", "source": "계산",
                "source_type": "REALTIME", "freshness": "FRESH", "reliability": 0.9,
                "unit": "%", "polarity": "HIGH_GOOD",
            },
            {
                "feature_id": "vix", "display_name": "VIX", "group": "공통 시장·거시",
                "value": 28.0, "numeric_value": 28.0, "status": "NEGATIVE", "source": "fixture",
                "source_type": "DELAYED", "freshness": "FRESH", "reliability": 0.8,
                "unit": "", "polarity": "LOW_GOOD",
            },
        ],
        "investor_flow": {}, "member_flow": {}, "orderbook": {}, "market_breadth": {},
        "sector_context": {}, "global_context": {}, "commodity_context": {},
        "rate_fx_context": {}, "news_context": {}, "dart_context": {},
        "position": {}, "data_quality": {"score": 90, "missing_feature_ids": [], "stale_feature_ids": [], "warnings": []},
        "statistical_context": {"available": False, "reason": "통계·백테스트 엔진 미연결"},
    }


@pytest.fixture
def service(tmp_path: Path) -> AiAnalysisService:
    settings = Settings(
        openai_api_key="",
        openai_model="gpt-5.6-sol",
        openai_fixture_mode=True,
        openai_analysis_enabled=True,
    )
    return AiAnalysisService(settings, Database(tmp_path / "test.sqlite3"), FeatureRegistry(ROOT / "config"))


def execute(service: AiAnalysisService, payload: dict | None = None) -> dict:
    return service.execute(
        payload or input_payload(),
        cancel=threading.Event(),
        progress=lambda message, percent: None,
    )


def test_openai_key_missing(service: AiAnalysisService) -> None:
    service.settings.openai_fixture_mode = False
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        execute(service)


def test_bad_key_error_is_safe(service: AiAnalysisService, monkeypatch: pytest.MonkeyPatch) -> None:
    service.settings.openai_fixture_mode = False
    service.settings.openai_api_key = "bad-key"
    monkeypatch.setattr("market_terminal.ai_analysis.provider_secret_verified", lambda *args: True)
    monkeypatch.setattr(service, "_openai_request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("OpenAI API 키가 올바르지 않습니다.")))
    with pytest.raises(RuntimeError, match="올바르지"):
        execute(service)


def test_timeout_error_isolated(service: AiAnalysisService, monkeypatch: pytest.MonkeyPatch) -> None:
    service.settings.openai_fixture_mode = False
    service.settings.openai_api_key = "test-key"
    monkeypatch.setattr("market_terminal.ai_analysis.provider_secret_verified", lambda *args: True)
    monkeypatch.setattr(service, "_openai_request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("OpenAI 분석이 60초 안에 끝나지 않았습니다.")))
    with pytest.raises(RuntimeError, match="60초"):
        execute(service)


def test_structured_fixture_success(service: AiAnalysisService) -> None:
    result = execute(service)["analysis"]
    assert result["overall_action"]["code"] in {"WAIT", "HOLD", "NO_TRADE"}
    assert len(result["top_features"]) <= 12
    assert result["historical_statistics"]["available"] is False
    assert result["conditional_trade_plan"]["automatic_order"] is False
    assert result["conditional_trade_plan"]["side"] in {"LONG", "SHORT", "NONE"}
    valid_ids = {item["feature_id"] for item in input_payload()["features"]}
    assert {
        item["feature_id"]
        for item in result["conditional_trade_plan"]["feature_triggers"]
    } <= valid_ids


def test_model_refusal_isolated(service: AiAnalysisService, monkeypatch: pytest.MonkeyPatch) -> None:
    service.settings.openai_fixture_mode = False
    service.settings.openai_api_key = "test-key"
    monkeypatch.setattr("market_terminal.ai_analysis.provider_secret_verified", lambda *args: True)
    monkeypatch.setattr(service, "_openai_request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("모델이 분석 요청을 거부했습니다.")))
    with pytest.raises(RuntimeError, match="거부"):
        execute(service)


def test_invalid_feature_ids_are_removed(service: AiAnalysisService) -> None:
    raw = service.fixture_response(input_payload())
    fake = copy.deepcopy(raw["top_features"][0])
    fake.update({"feature_id": "made_up_feature", "rank": 1})
    raw["top_features"].insert(0, fake)
    raw["feature_order"].insert(0, "made_up_feature")
    raw["alerts_to_register"].append({
        "feature_id": "made_up_feature", "operator": "GTE", "threshold": 1,
        "second_threshold": None, "severity": "DANGER", "basis": "AI_HEURISTIC",
        "explanation": "fake",
    })
    cleaned = service.sanitize(raw, input_payload())
    assert "made_up_feature" not in {row["feature_id"] for row in cleaned["top_features"]}
    assert "made_up_feature" not in cleaned["feature_order"]
    assert "made_up_feature" not in {row["feature_id"] for row in cleaned["alerts_to_register"]}


def test_fake_historical_statistics_are_always_stripped(service: AiAnalysisService) -> None:
    raw = service.fixture_response(input_payload())
    raw["historical_statistics"].update({"available": True, "sample_count": 999, "win_rate": 99.9, "expected_return_5m": 10.0})
    cleaned = service.sanitize(raw, input_payload())
    assert cleaned["historical_statistics"]["available"] is False
    assert cleaned["historical_statistics"]["sample_count"] is None
    assert cleaned["historical_statistics"]["win_rate"] is None
    assert cleaned["historical_statistics"]["expected_return_5m"] is None


def test_trade_plan_never_enables_real_automatic_order(service: AiAnalysisService) -> None:
    raw = service.fixture_response(input_payload())
    raw["conditional_trade_plan"]["automatic_order"] = True
    cleaned = service.sanitize(raw, input_payload())
    assert cleaned["conditional_trade_plan"]["automatic_order"] is False


def test_trade_debrief_uses_requested_saved_trade(service: AiAnalysisService) -> None:
    payload = input_payload()
    payload["trade_debrief_context"] = {
        "requested": True,
        "trade": {
            "id": 77,
            "status": "CLOSED",
            "side": "LONG",
            "net_pnl_krw": 12000,
        },
        "local_rule_analysis": {
            "summary": "가격과 수급 확인 뒤 진입해 이익 실현",
            "reasons": [{"detail": "가격 추세 확인"}],
        },
    }
    result = execute(service, payload)["analysis"]["trade_debrief"]
    assert result["applicable"] is True
    assert result["trade_id"] == 77
    assert result["outcome"] == "WIN"
    assert result["what_worked"]


def test_analysis_saved_to_sqlite(service: AiAnalysisService) -> None:
    analysis = execute(service)["analysis"]
    row = service.db.get_ai_analysis(analysis["analysis_id"])
    assert row is not None
    assert row["model"] == "gpt-5.6-sol"
    assert row["response_json"]["analysis_id"] == analysis["analysis_id"]


def test_ai_layout_apply_restore_and_pin(service: AiAnalysisService) -> None:
    stored = service.db.save_feature_layout({
        "symbol": "005930", "pinned": ["vix"], "excluded": [],
        "ai_order": ["ret_5", "vix"], "mode": "ai",
    })
    assert stored["pinned"] == ["vix"]
    assert stored["ai_order"][0] == "ret_5"
    restored = service.db.save_feature_layout({**stored, "ai_order": [], "mode": "default"})
    assert restored["mode"] == "default"
    assert restored["pinned"] == ["vix"]


def test_context_dependent_polarity() -> None:
    resolver = FeaturePolarityResolver()
    state, _ = resolver.resolve(
        {"feature_id": "usdkrw", "polarity": "CONTEXT_DEPENDENT"},
        1400,
        symbol_profile={"profile": "automobile_exporter"},
        related={"foreign_net": -10_000_000},
    )
    assert state == "CONFLICTED"


def test_alert_register_expiry_and_dedupe(service: AiAnalysisService) -> None:
    analysis = execute(service)["analysis"]
    evaluator = AlertEvaluator(service.db)
    first = evaluator.apply_from_analysis(analysis["analysis_id"], "005930")
    second = evaluator.apply_from_analysis(analysis["analysis_id"], "005930")
    assert len(first) == len(second)
    alerts = service.db.list_ai_alerts("005930", active_only=True)
    if alerts:
        values = {alerts[0]["feature_id"]: alerts[0]["threshold"]}
        evaluator.evaluate("005930", values)
        # A repeated evaluation inside the one-minute dedupe window creates no second event.
        assert evaluator.evaluate("005930", values) == []
    service.db.expire_ai_alerts((datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    assert service.db.list_ai_alerts("005930", active_only=True) == []


def test_openai_failure_does_not_damage_market_data(service: AiAnalysisService) -> None:
    with pytest.raises(RuntimeError):
        service.settings.openai_fixture_mode = False
        execute(service)
    # Independent market code remains importable and callable after the failure.
    from market_terminal.providers import DemoProvider
    assert not DemoProvider().get_bars("005930").empty
