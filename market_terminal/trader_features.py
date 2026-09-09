from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo("Asia/Seoul")


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _raw(context: dict[str, Any], key: str) -> tuple[Any, str, str, str | None]:
    value = context.get(key)
    if isinstance(value, dict):
        return (
            value.get("value"),
            str(value.get("source") or "외부 피드"),
            str(value.get("quality") or "LIVE"),
            value.get("direction"),
        )
    return value, "계산", "LIVE", None


def _direction(value: Any, positive: float = 0.0, negative: float = 0.0) -> str:
    number = _number(value)
    if number is None:
        return "neutral"
    if number > positive:
        return "positive"
    if number < negative:
        return "negative"
    return "neutral"


def _item(
    key: str,
    label: str,
    value: Any,
    unit: str = "",
    *,
    source: str = "계산",
    quality: str = "LIVE",
    direction: str | None = None,
) -> dict[str, Any]:
    if value is None:
        quality = quality if "미" in quality or "장외" in quality else "미제공"
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "source": source,
        "quality": quality,
        "direction": direction or _direction(value),
    }


def _from_context(
    context: dict[str, Any],
    key: str,
    label: str,
    unit: str = "",
    *,
    default_quality: str = "API 미연결",
) -> dict[str, Any]:
    value, source, quality, supplied = _raw(context, key)
    if value is None:
        source, quality = "API/외부 피드", default_quality
    return _item(
        key,
        label,
        value,
        unit,
        source=source,
        quality=quality,
        direction=supplied,
    )


def _microstructure(frame: pd.DataFrame, tick: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    calc_quality = "SIMULATED" if mode == "demo" else "LIVE"
    chart_source = "DEMO 차트 계산" if mode == "demo" else "체결 차트 계산"
    book_source = "KIS 호가" if mode == "kis" else "네이버 금융 공개 호가" if mode == "public" else "DEMO 호가"
    price = _number(tick.get("price"))
    orderbook = tick.get("orderbook") or {}
    asks = orderbook.get("asks") or []
    bids = orderbook.get("bids") or []
    best_ask = _number(asks[0].get("price")) if asks else _number(tick.get("ask"))
    best_bid = _number(bids[0].get("price")) if bids else _number(tick.get("bid"))
    ask_qty = sum(_number(row.get("qty")) or 0 for row in asks[:5])
    bid_qty = sum(_number(row.get("qty")) or 0 for row in bids[:5])
    depth = ask_qty + bid_qty
    spread_bps = (
        (best_ask - best_bid) / ((best_ask + best_bid) / 2) * 10_000
        if best_ask and best_bid and best_ask >= best_bid else None
    )
    imbalance = (bid_qty - ask_qty) / depth if depth > 0 else None
    microprice = (
        (best_ask * bid_qty + best_bid * ask_qty) / depth
        if best_ask and best_bid and depth > 0 else None
    )
    microprice_gap = (
        (microprice / price - 1) * 10_000 if microprice and price else None
    )

    vwap = relative_volume = day_position = acceleration = opening_breakout = None
    if frame is not None and not frame.empty:
        work = frame.copy()
        for column in ("open", "high", "low", "close", "volume"):
            work[column] = pd.to_numeric(work[column], errors="coerce")
        work = work.dropna(subset=["close"])
        if not work.empty:
            volume_sum = float(work["volume"].fillna(0).sum())
            if volume_sum > 0:
                vwap = float((work["close"] * work["volume"].fillna(0)).sum() / volume_sum)
            latest_volume = float(work.iloc[-1]["volume"] or 0)
            mean_volume = float(work["volume"].tail(20).mean() or 0)
            relative_volume = latest_volume / mean_volume if mean_volume > 0 else None
            high, low, close = float(work["high"].max()), float(work["low"].min()), float(work.iloc[-1]["close"])
            day_position = (close - low) / (high - low) * 100 if high > low else None
            if len(work) >= 4:
                ret_now = close / float(work.iloc[-2]["close"]) - 1
                ret_prev = float(work.iloc[-2]["close"]) / float(work.iloc[-3]["close"]) - 1
                acceleration = (ret_now - ret_prev) * 100
            if len(work) >= 6:
                opening_high = float(work.iloc[:5]["high"].max())
                opening_low = float(work.iloc[:5]["low"].min())
                if close > opening_high:
                    opening_breakout = (close / opening_high - 1) * 100
                elif close < opening_low:
                    opening_breakout = (close / opening_low - 1) * 100
                else:
                    opening_breakout = 0.0

    vwap_gap = (price / vwap - 1) * 100 if price and vwap else None
    return [
        _item("spread_bps", "최우선 호가 스프레드", None if spread_bps is None else round(spread_bps, 2), "bp",
              source=book_source, quality="LIVE" if spread_bps is not None else "호가 미제공",
              direction="warning" if spread_bps is not None and spread_bps > 15 else "neutral"),
        _item("orderbook_imbalance", "5단계 호가 불균형", None if imbalance is None else round(imbalance, 3), "",
              source=f"{book_source} 계산", quality="LIVE" if imbalance is not None else "호가 미제공",
              direction=_direction(imbalance, 0.12, -0.12)),
        _item("microprice_gap_bps", "미시가격-현재가 이격", None if microprice_gap is None else round(microprice_gap, 2), "bp",
              source=f"{book_source} 계산", quality="LIVE" if microprice_gap is not None else "호가 미제공",
              direction=_direction(microprice_gap, 1.0, -1.0)),
        _item("top5_depth_quantity", "5단계 총 호가잔량", round(depth) if depth > 0 else None, "주",
              source=book_source, quality="LIVE" if depth > 0 else "호가 미제공", direction="neutral"),
        _item("vwap_distance", "당일 VWAP 이격", None if vwap_gap is None else round(vwap_gap, 3), "%",
              source=chart_source, quality=calc_quality if vwap_gap is not None else "차트 미제공",
              direction=_direction(vwap_gap, 0.05, -0.05)),
        _item("relative_volume_1m", "1분 상대거래량(20)", None if relative_volume is None else round(relative_volume, 2), "배",
              source=chart_source, quality=calc_quality if relative_volume is not None else "차트 미제공",
              direction="positive" if relative_volume is not None and relative_volume >= 1.5 else "neutral"),
        _item("day_range_position", "당일 고저 범위 위치", None if day_position is None else round(day_position, 1), "%",
              source=chart_source, quality=calc_quality if day_position is not None else "차트 미제공",
              direction="positive" if day_position is not None and day_position >= 70 else "negative" if day_position is not None and day_position <= 30 else "neutral"),
        _item("price_acceleration_1m", "1분 가격 가속도", None if acceleration is None else round(acceleration, 3), "%p",
              source=chart_source, quality=calc_quality if acceleration is not None else "차트 미제공",
              direction=_direction(acceleration, 0.03, -0.03)),
        _item("opening_range_breakout", "시초 5분 범위 돌파", None if opening_breakout is None else round(opening_breakout, 3), "%",
              source=chart_source, quality=calc_quality if opening_breakout is not None else "차트 미제공",
              direction=_direction(opening_breakout, 0.0, 0.0)),
    ]


def _peer_features(
    selected_return: float | None,
    peer_rows: list[dict[str, Any]],
    market_return: float | None,
    mode: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    returns = [
        _number(row.get("change_rate"))
        for row in peer_rows
        if _number(row.get("change_rate")) is not None
    ]
    count = len(returns)
    up_count = sum(value > 0 for value in returns)
    down_count = sum(value < 0 for value in returns)
    strong_up = sum(value >= 1.0 for value in returns)
    strong_down = sum(value <= -1.0 for value in returns)
    avg_return = sum(returns) / count if count else None
    up_ratio = up_count / count * 100 if count else None
    volume_confirmed = [
        row for row in peer_rows
        if (_number(row.get("change_rate")) or 0) >= 1.0
        and (_number(row.get("volume_ratio")) or 0) >= 1.5
    ]
    attack_score = min(100.0, len(volume_confirmed) / count * 100) if count else None
    relative_peer = (
        selected_return - avg_return
        if selected_return is not None and avg_return is not None else None
    )
    relative_market = (
        selected_return - market_return
        if selected_return is not None and market_return is not None else None
    )

    if count == 0:
        keyword = "동종주 데이터 미제공"
        keyword_direction = "neutral"
    elif attack_score is not None and attack_score >= 50:
        keyword = "동종 기술주 공격적 동반상승"
        keyword_direction = "positive"
    elif up_ratio is not None and up_ratio >= 70:
        keyword = "동종주 동반상승"
        keyword_direction = "positive"
    elif up_ratio is not None and up_ratio <= 30:
        keyword = "동종주 동반하락"
        keyword_direction = "negative"
    else:
        keyword = "동종주 혼조"
        keyword_direction = "neutral"

    quality = ("SIMULATED" if mode == "demo" else "LIVE") if count else "동종주 시세 미제공"
    peer_source = "DEMO 동종주 계산" if mode == "demo" else "KIS 동종주 계산" if mode == "kis" else "네이버 공개 동종주 계산"
    items = [
        _item("peer_observed_count", "관측 동종주 수", count if count else None, "종목", source=peer_source, quality=quality, direction="neutral"),
        _item("peer_up_ratio", "동종주 상승 비율", None if up_ratio is None else round(up_ratio, 1), "%", source=peer_source, quality=quality,
              direction="positive" if up_ratio is not None and up_ratio >= 70 else "negative" if up_ratio is not None and up_ratio <= 30 else "neutral"),
        _item("peer_equal_weight_return", "동종주 동일가중 등락", None if avg_return is None else round(avg_return, 3), "%", source=peer_source, quality=quality,
              direction=_direction(avg_return, 0.1, -0.1)),
        _item("peer_joint_rise_count", "동반상승 동종주", strong_up if count else None, "종목", source=peer_source, quality=quality,
              direction="positive" if strong_up else "neutral"),
        _item("peer_joint_fall_count", "동반하락 동종주", strong_down if count else None, "종목", source=peer_source, quality=quality,
              direction="negative" if strong_down else "neutral"),
        _item("peer_attack_score", "동종주 공격 강도", None if attack_score is None else round(attack_score, 1), "점", source="가격+상대거래량 계산",
              quality=quality if count and all(row.get("volume_ratio") is not None for row in peer_rows) else "거래량 확인 제한",
              direction="positive" if attack_score is not None and attack_score >= 50 else "neutral"),
        _item("peer_relative_strength", "동종주 대비 상대강도", None if relative_peer is None else round(relative_peer, 3), "%p", source=peer_source, quality=quality,
              direction=_direction(relative_peer, 0.1, -0.1)),
        _item("kospi_relative_strength", "KOSPI 대비 상대강도", None if relative_market is None else round(relative_market, 3), "%p", source="DEMO 지수·종목 계산" if mode=="demo" else "KIS 지수·종목 계산" if mode=="kis" else "네이버 공개 지수·종목 계산",
              quality=quality if relative_market is not None else "지수/종목 시세 미제공", direction=_direction(relative_market, 0.1, -0.1)),
        _item("peer_movement_keyword", "동종주 대응 키워드", keyword, "", source="DEMO 가격·거래량 규칙" if mode=="demo" else "가격·거래량 규칙", quality=quality, direction=keyword_direction),
    ]
    keywords = [keyword]
    if strong_up:
        keywords.append(f"1% 이상 동반상승 {strong_up}종목")
    if volume_confirmed:
        keywords.append(f"거래량 동반 공격 {len(volume_confirmed)}종목")
    return items, keywords


MARKET_FEATURES = [
    ("kospi_index", "KOSPI", "pt"),
    ("kospi_change", "KOSPI 등락", "%"),
    ("kosdaq_index", "KOSDAQ", "pt"),
    ("kosdaq_change", "KOSDAQ 등락", "%"),
    ("kospi200_index", "KOSPI200", "pt"),
    ("kospi200_change", "KOSPI200 등락", "%"),
    ("kospi_breadth", "KOSPI 상승 종목 비율", "%"),
    ("kosdaq_breadth", "KOSDAQ 상승 종목 비율", "%"),
    ("kospi_kosdaq_spread", "KOSPI-KOSDAQ 강도차", "%p"),
    ("kospi200_futures_price", "KOSPI200 선물 최근월", "pt"),
    ("kospi200_futures_change", "KOSPI200 선물 등락", "%"),
    ("futures_basis", "선물 베이시스", "pt"),
    ("futures_open_interest", "선물 미결제약정", "계약"),
    ("futures_open_interest_change", "선물 미결제약정 증감", "계약"),
    ("futures_volume", "선물 누적 거래량", "계약"),
    ("night_futures_change", "KOSPI200 야간선물 등락", "%"),
    ("night_futures_gap", "야간선물-현물 괴리", "%p"),
    ("krx_derivatives_session", "KRX 파생 세션", ""),
    ("program_trading", "프로그램 순매수", "원"),
    ("program_arbitrage_net", "차익 프로그램 순매수", "원"),
    ("program_non_arbitrage_net", "비차익 프로그램 순매수", "원"),
    ("put_call_ratio", "풋콜 거래량 비율", ""),
    ("option_implied_volatility", "옵션 내재변동성", "%"),
    ("vkospi", "VKOSPI", "pt"),
    ("usdkrw_change", "USD/KRW 등락", "%"),
]


def compute_trader_features(
    frame: pd.DataFrame,
    tick: dict[str, Any],
    context: dict[str, Any],
    peer_rows: list[dict[str, Any]],
    mode: str = "kis",
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], list[str], list[str]]:
    """Build read-only trader context without filling unavailable real data."""
    market_items = [
        _from_context(
            context,
            key,
            label,
            unit,
            default_quality=(
                "장외/야간 값 미제공"
                if key in {"night_futures_change", "night_futures_gap"}
                else "API/외부 피드 미연결"
            ),
        )
        for key, label, unit in MARKET_FEATURES
    ]
    market_values = {item["key"]: item["value"] for item in market_items}
    kospi_change = _number(market_values.get("kospi_change"))
    kosdaq_change = _number(market_values.get("kosdaq_change"))
    if market_values.get("kospi_kosdaq_spread") is None and kospi_change is not None and kosdaq_change is not None:
        spread = round(kospi_change - kosdaq_change, 3)
        market_values["kospi_kosdaq_spread"] = spread
        for item in market_items:
            if item["key"] == "kospi_kosdaq_spread":
                item.update(value=spread, source="DEMO 지수 계산" if mode=="demo" else "KIS 지수 계산" if mode=="kis" else "네이버 공개 지수 계산", quality="SIMULATED" if mode=="demo" else "LIVE", direction=_direction(spread, 0.1, -0.1))
                break
    futures_change = _number(market_values.get("kospi200_futures_change"))
    if market_values.get("night_futures_gap") is None and futures_change is not None and kospi_change is not None:
        session = str(market_values.get("krx_derivatives_session") or "")
        if "야간" in session:
            gap = round(futures_change - kospi_change, 3)
            market_values["night_futures_gap"] = gap
            for item in market_items:
                if item["key"] == "night_futures_gap":
                    item.update(value=gap, source="DEMO 선물-현물 계산" if mode=="demo" else "KIS 선물-현물 계산" if mode=="kis" else "공개 선물-현물 계산", quality="SIMULATED" if mode=="demo" else "LIVE/야간", direction=_direction(gap, 0.1, -0.1))
                    break

    selected_return = _number(tick.get("change_rate"))
    peer_items, keywords = _peer_features(selected_return, peer_rows, kospi_change, mode)
    micro_items = _microstructure(frame, tick, mode)
    groups = {
        "주요 시장·야간선물": market_items,
        "동종주·테마 동반 움직임": peer_items,
        "전문 트레이더 미시구조": micro_items,
    }
    flat = {item["key"]: item["value"] for items in groups.values() for item in items}
    promoted = [
        "night_futures_change", "kospi200_futures_change", "futures_basis",
        "kospi_change", "kosdaq_change", "kospi_breadth", "kosdaq_breadth",
        "peer_movement_keyword", "peer_attack_score", "peer_up_ratio",
        "orderbook_imbalance", "vwap_distance", "relative_volume_1m",
    ]
    return groups, flat, promoted, keywords


def market_watch_cards(flat: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"feature_id": "kospi_change", "label": "KOSPI", "value": flat.get("kospi_change"), "unit": "%"},
        {"feature_id": "kosdaq_change", "label": "KOSDAQ", "value": flat.get("kosdaq_change"), "unit": "%"},
        {"feature_id": "night_futures_change", "label": "야간선물", "value": flat.get("night_futures_change"), "unit": "%"},
        {"feature_id": "peer_movement_keyword", "label": "동종주", "value": flat.get("peer_movement_keyword"), "unit": ""},
    ]


def build_semi_auto_playbook(
    analysis_record: dict[str, Any],
    snapshot: dict[str, Any],
    portfolio: dict[str, Any],
) -> dict[str, Any]:
    """Create a virtual-order prefill only; never call a trading endpoint."""
    analysis = analysis_record.get("response_json") or {}
    action = analysis.get("overall_action") or {}
    plan = analysis.get("conditional_trade_plan") or {}
    quality = analysis.get("data_quality") or {}
    request_quality = (analysis_record.get("request_json") or {}).get("data_quality") or {}
    current_price = _number(snapshot.get("flat", {}).get("price"))
    if current_price is None:
        current_price = _number((snapshot.get("tick") or {}).get("price"))
    mode = snapshot.get("mode")
    plan_side = plan.get("side") if plan.get("side") in {"LONG", "SHORT"} else (
        "LONG" if action.get("code") in {"STRONG_BUY_REVIEW", "BUY_REVIEW"} else
        "SHORT" if action.get("code") in {"SELL_REVIEW", "REDUCE_REVIEW"} else "NONE"
    )
    plan_applicable = bool(plan.get("applicable")) if plan else plan_side != "NONE"
    directional_review = (
        plan_side == "LONG" and action.get("code") in {"STRONG_BUY_REVIEW", "BUY_REVIEW"}
        or plan_side == "SHORT" and action.get("code") in {"SELL_REVIEW", "REDUCE_REVIEW"}
    )
    fresh_enough = int(quality.get("score") or 0) >= 60
    analysis_is_live = request_quality.get("mode") in {"kis", "public"}
    not_expired = False
    try:
        expires_at = datetime.fromisoformat(str(analysis.get("expires_at") or ""))
        compare_now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.now(KST).replace(tzinfo=None)
        not_expired = expires_at > compare_now
    except ValueError:
        pass
    ready = bool(
        directional_review and plan_applicable and fresh_enough and current_price and mode in {"kis", "public"}
        and analysis_is_live and not_expired
    )
    status = "READY" if ready else "WAIT"
    cash = max(0.0, _number(portfolio.get("cash")) or 0.0)
    allocation = min(cash * 0.10, cash)
    entry_prices = [
        _number(plan.get("entry_price_low_krw")),
        _number(plan.get("entry_price_high_krw")),
    ]
    entry_prices = [value for value in entry_prices if value and value > 0]
    reference_price = sum(entry_prices) / len(entry_prices) if entry_prices else current_price
    quantity = int(allocation // reference_price) if ready and reference_price else 0
    trigger_features = [
        {
            "feature_id": item.get("feature_id"),
            "display_name": item.get("display_name"),
            "current_display": item.get("current_display"),
            "direction": item.get("direction_now"),
            "reason": item.get("reason"),
        }
        for item in (analysis.get("top_features") or [])[:5]
    ]
    reasons: list[str] = []
    if not directional_review:
        reasons.append("GPT 종합 의견과 LONG/SHORT 계획 방향이 일치하지 않음")
    if not plan_applicable:
        reasons.append("GPT 조건부 거래 계획이 현재 진입 없음 상태")
    if not fresh_enough:
        reasons.append("데이터 품질 60점 미만")
    if mode not in {"kis", "public"}:
        reasons.append("DEMO 가상 데이터에서는 대응 입력값을 만들지 않음")
    if not analysis_is_live:
        reasons.append("GPT 분석 입력이 실제 공개/KIS 데이터 모드가 아님")
    if not not_expired:
        reasons.append("GPT 분석이 만료됐거나 만료시각을 확인할 수 없음")
    if not current_price:
        reasons.append("유효한 현재가 없음")
    if not reasons:
        reasons.append("GPT 주목 피처와 현재 실데이터 시세를 확인함")
    return {
        "analysis_id": analysis.get("analysis_id") or analysis_record.get("id"),
        "symbol": analysis_record.get("symbol"),
        "status": status,
        "status_label": "가상주문 입력 준비" if ready else "조건 대기",
        "reasons": reasons,
        "trigger_features": trigger_features,
        "attention_keywords": snapshot.get("attention_keywords") or [],
        "suggested_virtual_order": {
            "side": plan_side if ready else "NONE",
            "reference_price_krw": round(reference_price) if reference_price else None,
            "entry_price_low_krw": plan.get("entry_price_low_krw"),
            "entry_price_high_krw": plan.get("entry_price_high_krw"),
            "stop_loss_price_krw": plan.get("stop_loss_price_krw"),
            "stop_loss_pct": plan.get("stop_loss_pct"),
            "take_profit_1_price_krw": plan.get("take_profit_1_price_krw"),
            "take_profit_1_pct": plan.get("take_profit_1_pct"),
            "max_allocation_krw": round(allocation) if ready else 0,
            "quantity": quantity,
            "allocation_limit_percent": 10,
        },
        "manual_confirmation_required": True,
        "automatic_order": False,
        "real_order_supported": False,
        "statistics_available": False,
        "notice": "입력값만 채웁니다. 실제 주문은 없으며 가상 매수 버튼도 사용자가 직접 눌러야 합니다.",
        "created_at": datetime.now(KST).isoformat(),
    }
