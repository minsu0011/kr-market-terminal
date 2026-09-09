from __future__ import annotations

from pathlib import Path

from market_terminal.config import Settings
from market_terminal.database import Database
from market_terminal.trading import entry_cost, exit_result


def snapshot(price: float) -> dict:
    return {
        "price": {"current": price},
        "features": [
            {
                "feature_id": "ret_5",
                "display_name": "5분 수익률",
                "numeric_value": 1.0 if price >= 100 else -1.0,
                "status": "POSITIVE" if price >= 100 else "NEGATIVE",
            }
        ],
    }


def test_short_virtual_trade_profits_when_price_falls() -> None:
    settings = Settings(fee_rate=0, tax_rate=0, slippage_rate=0)
    entry = entry_cost(100, 10, settings)
    result = exit_result(
        100,
        90,
        10,
        settings,
        entry["fee"],
        snapshot(100),
        snapshot(90),
        side="SHORT",
    )
    assert result["gross_pnl"] == 100
    assert result["net_pnl"] == 100
    assert result["return_pct"] == 10
    assert result["analysis"]["side"] == "SHORT"


def test_short_virtual_trade_loses_when_price_rises() -> None:
    settings = Settings(fee_rate=0, tax_rate=0, slippage_rate=0)
    result = exit_result(
        100,
        110,
        10,
        settings,
        0,
        snapshot(100),
        snapshot(110),
        side="SHORT",
    )
    assert result["gross_pnl"] == -100
    assert result["return_pct"] == -10
    assert result["analysis"]["side"] == "SHORT"


def test_database_persists_short_side_and_migrates_old_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite3"
    db = Database(db_path)
    created = db.create_trade(
        {
            "symbol": "005930",
            "symbol_name": "삼성전자",
            "mode": "virtual",
            "side": "SHORT",
            "entry_time": "2026-07-31T00:00:00+00:00",
            "entry_price": 100,
            "entry_quantity": 10,
            "entry_amount": 1000,
            "entry_fee": 0,
            "entry_features": snapshot(100),
        }
    )
    assert created["side"] == "SHORT"
    assert db.get_trade(created["id"])["side"] == "SHORT"
