from __future__ import annotations
from typing import Any


def _num(features: dict[str, Any], key: str) -> float | None:
    value = features.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def analyze_trade(
    entry: dict[str, Any],
    exit_: dict[str, Any],
    net_pnl: float,
    return_pct: float,
    side: str = "LONG",
) -> dict[str, Any]:
    win = net_pnl > 0
    normalized_side = "SHORT" if str(side).upper() == "SHORT" else "LONG"
    reasons: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    ret5, ema, rsi = _num(entry, "ret_5"), _num(entry, "ema_gap"), _num(entry, "rsi14")
    volz, foreign, institution = _num(entry, "volume_z"), _num(entry, "foreign_net"), _num(entry, "institution_net")
    accel, breadth, vix = _num(entry, "flow_acceleration"), _num(entry, "decline_ratio"), _num(entry, "vix")
    short_ratio, news_risk = _num(entry, "short_ratio"), _num(entry, "negative_news")
    top_member_net, exit_member_net = _num(entry, "top_member_net"), _num(entry, "exit_member_net")
    top_member_name, exit_member_name = entry.get("top_member_name"), entry.get("exit_member_name")
    dart_risk = max((_num(entry, key) or 0) for key in ("dart_rights_issue", "dart_cb_bw", "dart_embezzlement", "dart_default", "dart_audit"))
    if win and normalized_side == "SHORT":
        if ret5 is not None and ret5 < -.15: reasons.append({"title":"하락 추세 동행","detail":f"SHORT 진입 시 5분 수익률이 {ret5:.2f}%로 하락 흐름에 동행했습니다."})
        if ema is not None and ema < 0: reasons.append({"title":"EMA 하방 구조","detail":f"EMA9-EMA21 이격이 {ema:.2f}%로 SHORT 방향을 지지했습니다."})
        if volz is not None and volz > 1: reasons.append({"title":"하락 거래량 확인","detail":f"거래량 z-score {volz:.2f}로 하락 움직임에 체결이 동반됐습니다."})
        if foreign is not None and institution is not None and foreign < 0 and institution < 0:
            reasons.append({"title":"외국인·기관 동시 이탈","detail":f"외국인 {foreign:+.1f}, 기관 {institution:+.1f}로 SHORT 수급 방향이 일치했습니다."})
        if breadth is not None and breadth > 65: reasons.append({"title":"시장 breadth 하락 우세","detail":f"하락 종목 비율 {breadth:.1f}%가 SHORT 방향을 지지했습니다."})
    elif not win and normalized_side == "SHORT":
        if ret5 is not None and ret5 > .1: reasons.append({"title":"상승 추세에 역행한 SHORT","detail":f"진입 시 5분 수익률이 +{ret5:.2f}%인데 하락 확인 없이 SHORT 진입했습니다."})
        if ema is not None and ema > 0: reasons.append({"title":"EMA 상승 구조 역행","detail":f"EMA9-EMA21 이격이 +{ema:.2f}%로 상승 구조였습니다."})
        if foreign is not None and institution is not None and foreign > 0 and institution > 0:
            reasons.append({"title":"외국인·기관 동시 유입 역행","detail":f"외국인 {foreign:+.1f}, 기관 {institution:+.1f} 매수에 SHORT가 역행했습니다."})
        if rsi is not None and rsi <= 30: reasons.append({"title":"과매도 구간 SHORT 추격","detail":f"RSI {rsi:.1f}에서 추가 SHORT 진입해 반등 위험이 컸습니다."})
    elif win:
        if ret5 is not None and ret5 > .15: reasons.append({"title":"단기 추세 동행","detail":f"진입 시 5분 수익률이 +{ret5:.2f}%로 상승 흐름에 동행했습니다."})
        if ema is not None and ema > 0: reasons.append({"title":"EMA 구조 우위","detail":f"EMA9가 EMA21보다 {ema:.2f}% 위에 있어 단기 추세가 지지됐습니다."})
        if volz is not None and volz > 1: reasons.append({"title":"거래량 확인","detail":f"거래량 z-score {volz:.2f}로 가격 움직임에 체결 강도가 동반됐습니다."})
        if foreign is not None and institution is not None and foreign > 0 and institution > 0:
            reasons.append({"title":"외국인·기관 동시 유입","detail":f"외국인 {foreign:+.1f}, 기관 {institution:+.1f}로 수급 방향이 일치했습니다."})
        if accel is not None and accel > 0: reasons.append({"title":"수급 가속","detail":f"수급가속도 {accel:+.2f}로 매수 강도가 증가했습니다."})
        if top_member_net is not None and top_member_net > 0 and top_member_name:
            reasons.append({"title":"특정 회원사 창구 유입","detail":f"{top_member_name} 창구가 {top_member_net:+,.0f}주 순매수로 상단 체결을 지지했습니다."})
        if rsi is not None and 40 <= rsi <= 68: reasons.append({"title":"과열 전 진입","detail":f"RSI {rsi:.1f}로 과매수 이전의 추세 구간이었습니다."})
    else:
        if rsi is not None and rsi >= 72: reasons.append({"title":"과열 추격","detail":f"진입 RSI가 {rsi:.1f}로 높아 되돌림 위험이 컸습니다."})
        if ret5 is not None and ret5 < -.1: reasons.append({"title":"하락 추세 역행","detail":f"진입 시 5분 수익률이 {ret5:.2f}%였는데 반등 확인 없이 매수했습니다."})
        if ema is not None and ema < 0: reasons.append({"title":"EMA 역배열","detail":f"EMA9-EMA21 이격이 {ema:.2f}%로 단기 추세가 하방이었습니다."})
        if foreign is not None and institution is not None and foreign < 0 and institution < 0:
            reasons.append({"title":"외국인·기관 동시 이탈","detail":f"외국인 {foreign:+.1f}, 기관 {institution:+.1f}로 핵심 수급이 동시에 이탈했습니다."})
        if breadth is not None and breadth > 65: reasons.append({"title":"시장 breadth 악화","detail":f"하락 종목 비율 {breadth:.1f}%로 시장 전염이 우세했습니다."})
        if vix is not None and vix > 25: reasons.append({"title":"글로벌 위험 회피","detail":f"VIX {vix:.1f} 환경에서 위험자산 단타 실패 확률이 높았습니다."})
        if short_ratio is not None and short_ratio > 8: reasons.append({"title":"공매도 압력","detail":f"공매도 거래비중 {short_ratio:.1f}%가 상단을 제약했습니다."})
        if news_risk is not None and news_risk > .65: reasons.append({"title":"뉴스 위험","detail":f"부정 뉴스 점수 {news_risk:.2f}가 높았습니다."})
        if exit_member_net is not None and exit_member_net < 0 and exit_member_name:
            reasons.append({"title":"특정 회원사 창구 이탈","detail":f"{exit_member_name} 창구가 {exit_member_net:,.0f}주 순매도로 매도 압력을 높였습니다."})
        if dart_risk > 0: reasons.append({"title":"공시 이벤트 위험","detail":"자금조달·감사의견·법적 위험 계열 공시 플래그가 있었습니다."})
    exit_ret5, exit_ema = _num(exit_, "ret_5"), _num(exit_, "ema_gap")
    if win and exit_ret5 is not None and exit_ret5 < 0: warnings.append({"title":"청산 타이밍 양호","detail":"청산 시 5분 모멘텀이 음수로 전환돼 이익 보존에 유리했습니다."})
    elif not win and exit_ema is not None and exit_ema < 0: warnings.append({"title":"손절 근거","detail":"청산 시 EMA 역배열이 유지돼 추가 하락 위험을 줄였습니다."})
    if not reasons: reasons.append({"title":"가격 경로 영향","detail":"단일 지배 피처가 뚜렷하지 않아 체결 위치와 단기 가격 경로가 손익을 주도했습니다."})
    comparison_keys = {
        "ret_5": "5분 추세", "ema_gap": "EMA 이격", "rsi14": "RSI",
        "volume_z": "거래량 z-score", "relative_volume_1m": "1분 상대거래량",
        "vwap_distance": "VWAP 이격", "orderbook_imbalance": "호가 불균형",
        "foreign_net": "외국인 순매수", "institution_net": "기관 순매수",
        "flow_acceleration": "수급 가속", "decline_ratio": "하락 종목 비율",
    }
    changes: list[dict[str, Any]] = []
    for key, label in comparison_keys.items():
        before, after = _num(entry, key), _num(exit_, key)
        if before is None or after is None:
            continue
        changes.append({
            "feature_id": key,
            "label": label,
            "entry": round(before, 4),
            "exit": round(after, 4),
            "change": round(after - before, 4),
        })
    changes.sort(key=lambda item: abs(item["change"]), reverse=True)

    exit_notes: list[str] = []
    if exit_ret5 is not None:
        exit_notes.append(f"청산 시 5분 추세 {exit_ret5:+.2f}%")
    if exit_ema is not None:
        exit_notes.append(f"청산 시 EMA 이격 {exit_ema:+.2f}%")
    entry_assessment = " · ".join(item["detail"] for item in reasons[:2])
    exit_assessment = " · ".join(exit_notes) if exit_notes else "청산 시점의 핵심 추세 피처가 충분히 저장되지 않았습니다."
    next_checklist = [
        "진입 전에 추세·거래량·수급 중 최소 두 독립 그룹을 확인",
        "진입과 동시에 원화 손절가·손절률·1차 익절가를 기록",
        "기관 투자자 수급과 증권사 회원사 창구를 별도로 재확인",
    ]
    if not win:
        next_checklist.insert(0, "손실 원인 피처가 해소되기 전 같은 방향 재진입 금지")
    elif exit_ret5 is not None and exit_ret5 > 0:
        next_checklist.insert(0, "수익 거래도 청산 뒤 추가 상승 여부를 기록해 조기청산을 점검")
    result = "승리" if win else "패배"
    worked = [item["detail"] for item in reasons[:6]] if win else [
        item["detail"] for item in warnings if item["title"] in {"손절 근거", "청산 타이밍 양호"}
    ]
    failed = [item["detail"] for item in reasons[:6]] if not win else [
        item["detail"] for item in warnings if item["title"] not in {"청산 타이밍 양호"}
    ]
    return {
        "result": result,
        "side": normalized_side,
        "net_pnl": net_pnl,
        "return_pct": return_pct,
        "reasons": reasons[:6],
        "warnings": warnings[:3],
        "what_worked": worked,
        "what_failed": failed,
        "entry_assessment": entry_assessment,
        "exit_assessment": exit_assessment,
        "feature_changes": changes[:8],
        "next_time_checklist": next_checklist[:5],
        "statistics_available": False,
        "automatic_order": False,
        "summary": f"{result}: 순손익 {net_pnl:,.0f}원, 수익률 {return_pct:+.3f}%. {reasons[0]['detail']}",
    }
