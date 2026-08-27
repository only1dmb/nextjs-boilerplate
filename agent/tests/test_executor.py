"""Sizing, mode gating, and the live-arm requirement."""

from dataclasses import replace

import pytest

from robinhood_agent.config import LIVE_ARM_ENV, LIVE_ARM_VALUE, ConfigError
from robinhood_agent.executor import Executor, size_intent
from robinhood_agent.journal import Journal
from robinhood_agent.strategies import Signal


PRICES = {"AAPL": 110.0, "MSFT": 400.0}


# -- sizing ----------------------------------------------------------------


def test_buy_takes_the_tightest_binding_limit(snapshot, config):
    """The $1,000 per-order cap is not the binding constraint here.

    AAPL already holds $1,100 of a $10k book against a 20% cap, so only $900
    of headroom remains: 8 shares at $110, not the 9 the cap alone would allow.
    """
    intent = size_intent(Signal("AAPL", "buy", 1.0, "test"), snapshot, config, PRICES)
    assert intent.quantity == 8
    assert intent.notional <= config.risk.max_order_notional


def test_buy_sized_to_per_order_cap_when_it_binds(snapshot, config):
    snapshot.positions.clear()  # no existing position, so headroom is $2,000
    intent = size_intent(Signal("AAPL", "buy", 1.0, "test"), snapshot, config, PRICES)
    assert intent.quantity == 9  # $1,000 cap / $110


def test_buy_scaled_by_signal_strength(snapshot, config):
    intent = size_intent(Signal("AAPL", "buy", 0.5, "half"), snapshot, config, PRICES)
    assert intent.quantity == 4  # $500 / $110


def test_buy_limited_by_concentration_headroom(snapshot, config):
    """AAPL holds $1,100 of $10k; a 20% cap leaves $900 of headroom."""
    intent = size_intent(Signal("AAPL", "buy", 1.0, "test"), snapshot, config, PRICES)
    assert intent.quantity * 110.0 <= 0.20 * snapshot.equity - 1100.0 + 110.0


def test_buy_limited_by_cash_buffer(snapshot, config):
    snapshot.cash = 340.0  # $240 usable above the $100 buffer
    intent = size_intent(Signal("AAPL", "buy", 1.0, "test"), snapshot, config, PRICES)
    assert intent.quantity == 2


def test_buy_returns_none_when_a_share_is_unaffordable(snapshot, config):
    snapshot.cash = 150.0
    assert size_intent(Signal("AAPL", "buy", 1.0, "x"), snapshot, config, PRICES) is None


def test_strategy_requested_notional_is_still_capped(snapshot, config):
    signal = Signal("AAPL", "buy", 1.0, "rebalance", details={"notional": 99_000})
    intent = size_intent(signal, snapshot, config, PRICES)
    assert intent.notional <= config.risk.max_order_notional


def test_full_exit_sells_whole_position(snapshot, config):
    intent = size_intent(Signal("AAPL", "sell", 1.0, "exit"), snapshot, config, PRICES)
    assert intent.quantity == 10


def test_fractional_remainder_still_exits(snapshot, config):
    snapshot.positions["AAPL"].quantity = 0.4
    intent = size_intent(Signal("AAPL", "sell", 1.0, "exit"), snapshot, config, PRICES)
    assert intent.quantity == pytest.approx(0.4)


def test_sell_with_nothing_held_is_a_non_event(snapshot, config):
    assert size_intent(Signal("MSFT", "sell", 1.0, "x"), snapshot, config, PRICES) is None


def test_missing_price_is_a_non_event(snapshot, config):
    assert size_intent(Signal("AAPL", "buy", 1.0, "x"), snapshot, config, {}) is not None
    snapshot.positions.clear()
    assert size_intent(Signal("AAPL", "buy", 1.0, "x"), snapshot, config, {}) is None


# -- mode gating -----------------------------------------------------------


class RecordingClient:
    """Fails loudly if the agent tries to trade when it should not."""

    def __init__(self):
        self.orders = []

    def submit_limit(self, symbol, side, quantity, limit_price, tif, ext):
        self.orders.append((symbol, side, quantity, limit_price))
        return {"id": "order-1", "state": "queued"}

    def submit_market(self, symbol, side, quantity, tif, ext):
        self.orders.append((symbol, side, quantity, "market"))
        return {"id": "order-2", "state": "queued"}


def make_executor(config, tmp_path, confirm=None):
    client = RecordingClient()
    journal = Journal(tmp_path / "journal.jsonl")
    return client, journal, Executor(client, config, journal, confirm_fn=confirm)


def test_paper_mode_never_touches_the_broker(config, ctx, tmp_path):
    from robinhood_agent.risk import TradeIntent

    client, journal, executor = make_executor(config, tmp_path)
    result = executor.execute(TradeIntent("AAPL", "buy", 1, 110.0), ctx)

    assert result.placed
    assert result.mode == "paper"
    assert client.orders == []
    assert any(r["event"] == "order_submitted" and r["simulated"] for r in journal.read())


def test_confirm_mode_declines_without_a_yes(config, ctx, tmp_path):
    from robinhood_agent.risk import TradeIntent

    config = replace(config, mode="confirm")
    ctx.config = config
    client, journal, executor = make_executor(config, tmp_path, confirm=lambda _: False)

    result = executor.execute(TradeIntent("AAPL", "buy", 1, 110.0), ctx)
    assert not result.placed
    assert client.orders == []


def test_confirm_mode_places_on_yes(config, ctx, tmp_path):
    from robinhood_agent.risk import TradeIntent

    config = replace(config, mode="confirm")
    ctx.config = config
    client, journal, executor = make_executor(config, tmp_path, confirm=lambda _: True)

    result = executor.execute(TradeIntent("AAPL", "buy", 1, 110.0), ctx)
    assert result.placed
    assert client.orders == [("AAPL", "buy", 1, 110.22)]  # 0.2% through the quote


def test_live_mode_refuses_without_the_env_arm(config, ctx, tmp_path, monkeypatch):
    from robinhood_agent.risk import TradeIntent

    monkeypatch.delenv(LIVE_ARM_ENV, raising=False)
    config = replace(config, mode="live")
    ctx.config = config
    client, _, executor = make_executor(config, tmp_path)

    with pytest.raises(ConfigError):
        executor.execute(TradeIntent("AAPL", "buy", 1, 110.0), ctx)
    assert client.orders == []


def test_live_mode_places_when_armed(config, ctx, tmp_path, monkeypatch):
    from robinhood_agent.risk import TradeIntent

    monkeypatch.setenv(LIVE_ARM_ENV, LIVE_ARM_VALUE)
    config = replace(config, mode="live")
    ctx.config = config
    client, _, executor = make_executor(config, tmp_path)

    result = executor.execute(TradeIntent("AAPL", "buy", 1, 110.0), ctx)
    assert result.placed
    assert len(client.orders) == 1


def test_risk_rejection_stops_a_live_order(config, ctx, tmp_path, monkeypatch):
    from robinhood_agent.risk import TradeIntent

    monkeypatch.setenv(LIVE_ARM_ENV, LIVE_ARM_VALUE)
    config = replace(config, mode="live")
    ctx.config = config
    ctx.halted = True
    client, _, executor = make_executor(config, tmp_path)

    result = executor.execute(TradeIntent("AAPL", "buy", 1, 110.0), ctx)
    assert not result.placed
    assert client.orders == []


def test_sell_limit_rests_below_the_quote(config, ctx, tmp_path, monkeypatch):
    from robinhood_agent.risk import TradeIntent

    monkeypatch.setenv(LIVE_ARM_ENV, LIVE_ARM_VALUE)
    config = replace(config, mode="live")
    ctx.config = config
    client, _, executor = make_executor(config, tmp_path)

    executor.execute(TradeIntent("AAPL", "sell", 5, 110.0), ctx)
    assert client.orders[0][3] == 109.78
