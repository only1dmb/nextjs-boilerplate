import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from robinhood_agent.client import AccountSnapshot, Position  # noqa: E402
from robinhood_agent.config import Config, RiskLimits, StrategyConfig  # noqa: E402
from robinhood_agent.risk import RiskContext  # noqa: E402


@pytest.fixture
def limits():
    return RiskLimits(
        max_order_notional=1000.0,
        min_order_notional=1.0,
        max_position_pct=0.20,
        min_cash_buffer=100.0,
        max_trades_per_day=5,
        max_daily_drawdown_pct=0.03,
        per_symbol_cooldown_minutes=60,
        symbol_allowlist=["AAPL", "MSFT"],
    )


@pytest.fixture
def config(limits, tmp_path):
    return Config(
        mode="paper",
        risk=limits,
        strategies=[StrategyConfig(name="sma_crossover", symbols=["AAPL"])],
        state_dir=tmp_path / "state",
    )


@pytest.fixture
def snapshot():
    return AccountSnapshot(
        equity=10_000.0,
        cash=5_000.0,
        buying_power=5_000.0,
        positions={
            "AAPL": Position("AAPL", quantity=10, average_buy_price=100.0, price=110.0),
        },
        taken_at=datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def ctx(config, snapshot):
    return RiskContext(
        config=config,
        snapshot=snapshot,
        halted=False,
        market_open=True,
        open_order_symbols=set(),
        trades_today=0,
        day_trade_count=0,
        day_open_equity=10_000.0,
        last_trade_at={},
        now=datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc),
    )
