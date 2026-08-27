"""The risk stack is what stands between a buggy strategy and real money."""

from datetime import timedelta

import pytest

from robinhood_agent.risk import TradeIntent, evaluate


def buy(symbol="AAPL", quantity=1, price=110.0):
    return TradeIntent(symbol, "buy", quantity, price, strategy="test")


def sell(symbol="AAPL", quantity=1, price=110.0):
    return TradeIntent(symbol, "sell", quantity, price, strategy="test")


def rejected_by(intent, ctx, fragment):
    decision = evaluate(intent, ctx)
    assert not decision.allowed
    assert any(fragment in r for r in decision.rejections), decision.rejections


def test_clean_buy_passes(ctx):
    assert evaluate(buy(), ctx).allowed


def test_clean_sell_passes(ctx):
    assert evaluate(sell(quantity=5), ctx).allowed


def test_halt_blocks_everything(ctx):
    ctx.halted = True
    rejected_by(buy(), ctx, "halted")
    rejected_by(sell(), ctx, "halted")


def test_closed_market_blocks(ctx):
    ctx.market_open = False
    rejected_by(buy(), ctx, "market is closed")


def test_unknown_market_hours_blocks(ctx):
    """Unknown is not the same as open -- the agent refuses to guess."""
    ctx.market_open = None
    rejected_by(buy(), ctx, "could not confirm market hours")


def test_extended_hours_opt_in_allows_closed_market(ctx, config):
    from dataclasses import replace

    ctx.config = replace(config, allow_extended_hours=True)
    ctx.market_open = False
    assert evaluate(buy(), ctx).allowed


def test_symbol_must_be_allowlisted(ctx):
    rejected_by(buy(symbol="TSLA"), ctx, "not in risk.symbol_allowlist")


def test_order_over_notional_cap_blocked(ctx):
    rejected_by(buy(quantity=100, price=110.0), ctx, "max_order_notional")


def test_order_under_min_notional_blocked(ctx):
    rejected_by(buy(quantity=1, price=0.50), ctx, "min_order_notional")


def test_concentration_cap_counts_existing_position(ctx):
    """AAPL is already $1,100 of a $10k book; the cap is 20%."""
    rejected_by(buy(quantity=9, price=110.0), ctx, "max_position_pct")
    assert evaluate(buy(quantity=8, price=110.0), ctx).allowed


def test_cash_buffer_respected(ctx, snapshot):
    snapshot.cash = 150.0
    rejected_by(buy(quantity=1, price=110.0), ctx, "min_cash_buffer")


def test_cannot_sell_more_than_held(ctx):
    rejected_by(sell(quantity=11), ctx, "never opens shorts")


def test_cannot_short_a_symbol_not_held(ctx):
    rejected_by(sell(symbol="MSFT", quantity=1), ctx, "never opens shorts")


def test_daily_trade_cap(ctx):
    ctx.trades_today = 5
    rejected_by(buy(), ctx, "max_trades_per_day")


def test_pdt_guard_blocks_small_account(ctx):
    ctx.day_trade_count = 3
    rejected_by(buy(), ctx, "PDT threshold")


def test_pdt_guard_ignores_large_account(ctx, snapshot):
    snapshot.equity = 50_000.0
    ctx.day_trade_count = 9
    assert evaluate(buy(), ctx).allowed


def test_drawdown_circuit_breaker(ctx, snapshot):
    snapshot.equity = 9_600.0  # 4% below the 10,000 open
    rejected_by(buy(), ctx, "circuit breaker")


def test_drawdown_within_limit_passes(ctx, snapshot):
    snapshot.equity = 9_800.0  # 2%
    assert evaluate(buy(), ctx).allowed


def test_duplicate_order_blocked(ctx):
    ctx.open_order_symbols = {"AAPL"}
    rejected_by(buy(), ctx, "already working")


def test_cooldown_blocks_rapid_reentry(ctx):
    ctx.last_trade_at = {"AAPL": ctx.now - timedelta(minutes=10)}
    rejected_by(buy(), ctx, "cooldown")


def test_cooldown_expires(ctx):
    ctx.last_trade_at = {"AAPL": ctx.now - timedelta(minutes=61)}
    assert evaluate(buy(), ctx).allowed


@pytest.mark.parametrize("quantity", [0, -1])
def test_nonpositive_quantity_blocked(ctx, quantity):
    rejected_by(TradeIntent("AAPL", "buy", quantity, 110.0), ctx, "not positive")


def test_nonpositive_price_blocked(ctx):
    rejected_by(TradeIntent("AAPL", "buy", 1, 0.0), ctx, "not positive")


def test_all_rejections_are_collected(ctx):
    """The journal should show every reason, not just the first."""
    ctx.halted = True
    ctx.market_open = False
    decision = evaluate(buy(symbol="TSLA"), ctx)
    assert len(decision.rejections) >= 3
