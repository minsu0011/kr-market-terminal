from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd

from market_terminal.trader_features import (
    build_semi_auto_playbook,
    compute_trader_features,
)

ROOT = Path(__file__).resolve().parents[1]


def frame() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "time": f"2026-07-31T09:{minute:02d}:00+09:00",
            "open": 100 + minute,
            "high": 102 + minute,
            "low": 99 + minute,
            "close": 101 + minute,
            "volume": 1000 + minute * 200,
        }
        for minute in range(10)
    ])


def value(groups: dict, feature_id: str):
    for items in groups.values():
        for item in items:
            if item["key"] == feature_id:
                return item["value"]
    raise AssertionError(f"missing feature {feature_id}")


def test_market_night_futures_and_peer_attack_are_separate_features() -> None:
    context = {
        "kospi_change": {"value": 0.4, "source": "KIS", "quality": "LIVE"},
        "kosdaq_change": {"value": 0.9, "source": "KIS", "quality": "LIVE"},
        "kospi200_futures_change": {"value": 0.7, "source": "KIS", "quality": "LIVE"},
        "night_futures_change": {"value": 0.7, "source": "KIS", "quality": "LIVE/야간"},
        "futures_basis": {"value": 0.25, "source": "KIS", "quality": "LIVE"},
        "krx_derivatives_session": {"value": "야간", "source": "KIS", "quality": "LIVE"},
    }
    tick = {
        "price": 110,
        "change_rate": 1.4,
        "orderbook": {
            "asks": [{"price": 111, "qty": 300}, {"price": 112, "qty": 200}],
            "bids": [{"price": 110, "qty": 700}, {"price": 109, "qty": 500}],
        },
    }
    peers = [
        {"symbol": "000660", "change_rate": 1.8, "volume_ratio": 2.1},
        {"symbol": "035420", "change_rate": 1.2, "volume_ratio": 1.7},
    ]
    groups, flat, promoted, keywords = compute_trader_features(frame(), tick, context, peers)

    assert value(groups, "night_futures_change") == 0.7
    assert value(groups, "night_futures_gap") == 0.3
    assert value(groups, "peer_attack_score") == 100.0
    assert value(groups, "peer_movement_keyword") == "동종 기술주 공격적 동반상승"
    assert value(groups, "orderbook_imbalance") > 0
    assert "night_futures_change" in promoted
    assert any("공격" in keyword for keyword in keywords)
    assert flat["kospi_kosdaq_spread"] == -0.5


def test_unavailable_real_features_are_not_fabricated() -> None:
    groups, flat, _, _ = compute_trader_features(
        frame(),
        {"price": 110, "change_rate": 0.1},
        {"krx_derivatives_session": {"value": "장외", "source": "규칙", "quality": "장외"}},
        [],
    )
    assert value(groups, "night_futures_change") is None
    assert value(groups, "program_trading") is None
    assert value(groups, "peer_attack_score") is None
    assert flat["option_implied_volatility"] is None


def analysis_record() -> dict:
    return {
        "id": "analysis-1",
        "symbol": "005930",
        "request_json": {"data_quality": {"mode": "kis"}},
        "response_json": {
            "analysis_id": "analysis-1",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "overall_action": {"code": "BUY_REVIEW"},
            "data_quality": {"score": 82},
            "top_features": [
                {
                    "feature_id": "night_futures_change",
                    "display_name": "KOSPI200 야간선물 등락",
                    "current_display": "+0.7%",
                    "direction_now": "POSITIVE",
                    "reason": "야간 위험선호 확인",
                }
            ],
        },
    }


def test_semi_auto_playbook_only_prefills_virtual_order() -> None:
    playbook = build_semi_auto_playbook(
        analysis_record(),
        {"mode": "kis", "flat": {"price": 80_000}, "attention_keywords": ["동종주 동반상승"]},
        {"cash": 10_000_000},
    )
    assert playbook["status"] == "READY"
    assert playbook["suggested_virtual_order"]["max_allocation_krw"] == 1_000_000
    assert playbook["suggested_virtual_order"]["quantity"] == 12
    assert playbook["manual_confirmation_required"] is True
    assert playbook["automatic_order"] is False
    assert playbook["real_order_supported"] is False
    assert playbook["statistics_available"] is False


def test_demo_mode_never_prepares_order_values() -> None:
    playbook = build_semi_auto_playbook(
        analysis_record(),
        {"mode": "demo", "flat": {"price": 80_000}},
        {"cash": 10_000_000},
    )
    assert playbook["status"] == "WAIT"
    assert playbook["suggested_virtual_order"]["quantity"] == 0
    assert playbook["automatic_order"] is False


def test_browser_prepare_function_never_calls_trade_open() -> None:
    javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = javascript.index("async function prepareSemiAutoResponse")
    end = javascript.index("async function saveCurrentAi", start)
    function_source = javascript[start:end]
    assert "/api/ai/playbook/" in function_source
    assert "/api/trades/open" not in function_source
    assert "updateQuantity()" in function_source
