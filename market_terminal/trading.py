from __future__ import annotations
from typing import Any
from .analysis_engine import analyze_trade
from .config import Settings


def validate_quantity(quantity: int, total_cost: float, cash: float) -> None:
    if quantity <= 0: raise ValueError("수량은 1주 이상이어야 합니다.")
    if total_cost <= 0: raise ValueError("가격은 0보다 커야 합니다.")
    if total_cost > cash + 1e-9: raise ValueError("주문금액이 사용 가능한 현금을 초과합니다.")


def entry_cost(price: float, quantity: int, settings: Settings) -> dict[str, float]:
    gross = price * quantity
    fee = gross * (settings.fee_rate + settings.slippage_rate)
    return {"gross": gross, "fee": fee, "total": gross + fee}


def exit_result(entry_price: float, exit_price: float, quantity: int, settings: Settings,
                entry_fee: float, entry_features: dict[str, Any], exit_features: dict[str, Any],
                side: str = "LONG") -> dict[str, Any]:
    entry_gross, exit_gross = entry_price * quantity, exit_price * quantity
    exit_fee = exit_gross * (settings.fee_rate + settings.slippage_rate)
    tax = exit_gross * settings.tax_rate
    normalized_side = "SHORT" if str(side).upper() == "SHORT" else "LONG"
    gross_pnl = entry_gross - exit_gross if normalized_side == "SHORT" else exit_gross - entry_gross
    net_pnl = gross_pnl - entry_fee - exit_fee - tax
    return_pct = net_pnl / entry_gross * 100 if entry_gross else 0.0
    analysis = analyze_trade(entry_features, exit_features, net_pnl, return_pct, normalized_side)
    analysis["side"] = normalized_side
    return {"exit_amount":exit_gross,"exit_fee":exit_fee,"tax":tax,"gross_pnl":gross_pnl,
            "net_pnl":net_pnl,"return_pct":return_pct,"reason_summary":analysis["summary"],"analysis":analysis}
