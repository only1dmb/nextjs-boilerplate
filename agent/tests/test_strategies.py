"""Strategies must fire on transitions, not on states."""

import pytest

from robinhood_agent.indicators import pct_change, rsi, sma, sma_series
from robinhood_agent.strategies import MarketView, build


def view(symbol, closes, held=0.0, equity=10_000.0):
    price = closes[-1]
    return MarketView(
        prices={symbol: price},
        closes={symbol: closes},
        held_quantity={symbol: held} if held else {},
        held_value={symbol: held * price} if held else {},
        equity=equity,
    )


# -- indicators ------------------------------------------------------------


def test_sma_needs_a_full_window():
    assert sma([1, 2, 3], 5) is None
    assert sma([1, 2, 3, 4, 5], 5) == 3.0


def test_sma_series_aligns_with_sma():
    closes = [float(n) for n in range(1, 30)]
    assert sma_series(closes, 10)[-1] == pytest.approx(sma(closes, 10))
    assert sma_series(closes, 10)[8] is None
    assert sma_series(closes, 10)[9] is not None


def test_rsi_bounds():
    rising = [float(n) for n in range(1, 40)]
    falling = list(reversed(rising))
    assert rsi(rising, 14) == pytest.approx(100.0)
    assert rsi(falling, 14) == pytest.approx(0.0, abs=1e-6)


def test_rsi_needs_history():
    assert rsi([1, 2, 3], 14) is None


def test_pct_change():
    assert pct_change([100, 110], 1) == pytest.approx(0.10)
    assert pct_change([100], 5) is None


# -- sma crossover ---------------------------------------------------------


def rising_then_falling(n_up=80, n_down=80):
    return [100.0 + i for i in range(n_up)] + [
        100.0 + n_up - i for i in range(n_down)
    ]


def test_crossover_buys_only_on_the_cross():
    closes = [100.0] * 60 + [100.0 + i * 2 for i in range(1, 25)]
    strategy = build("sma_crossover", {"fast": 5, "slow": 20})

    signals = strategy.generate(["AAPL"], view("AAPL", closes))
    assert [s.side for s in signals] in ([], ["buy"])

    # Well past the cross with the trend intact: no repeat buy.
    later = closes + [closes[-1] + i for i in range(1, 20)]
    assert strategy.generate(["AAPL"], view("AAPL", later)) == []


def test_crossover_sells_only_what_is_held():
    closes = rising_then_falling()
    strategy = build("sma_crossover", {"fast": 5, "slow": 20})

    # Scan forward for the bar where the death cross happens.
    sell_seen = False
    for end in range(25, len(closes)):
        signals = strategy.generate(["AAPL"], view("AAPL", closes[:end], held=10))
        if any(s.side == "sell" for s in signals):
            sell_seen = True
            break
    assert sell_seen

    # The same bar, holding nothing, produces no sell.
    signals = strategy.generate(["AAPL"], view("AAPL", closes[:end], held=0))
    assert all(s.side != "sell" for s in signals)


def test_crossover_rejects_inverted_windows():
    strategy = build("sma_crossover", {"fast": 50, "slow": 20})
    with pytest.raises(ValueError, match="must be less than"):
        strategy.generate(["AAPL"], view("AAPL", [100.0] * 100))


def test_crossover_ignores_short_history():
    strategy = build("sma_crossover", {"fast": 20, "slow": 50})
    assert strategy.generate(["AAPL"], view("AAPL", [100.0] * 10)) == []


# -- rsi reversion ---------------------------------------------------------


def test_rsi_buys_oversold_when_flat():
    closes = [100.0 - i for i in range(40)]
    strategy = build("rsi_reversion", {"period": 14, "oversold": 30})
    signals = strategy.generate(["AAPL"], view("AAPL", closes))
    assert [s.side for s in signals] == ["buy"]


def test_rsi_does_not_double_up_when_already_held():
    closes = [100.0 - i for i in range(40)]
    strategy = build("rsi_reversion", {"period": 14, "oversold": 30})
    assert strategy.generate(["AAPL"], view("AAPL", closes, held=5)) == []


def test_rsi_sells_overbought_only_when_held():
    closes = [100.0 + i for i in range(40)]
    strategy = build("rsi_reversion", {"period": 14, "overbought": 70})
    assert [s.side for s in strategy.generate(["AAPL"], view("AAPL", closes, held=5))] == ["sell"]
    assert strategy.generate(["AAPL"], view("AAPL", closes, held=0)) == []


def test_rsi_rejects_inverted_thresholds():
    strategy = build("rsi_reversion", {"oversold": 80, "overbought": 20})
    with pytest.raises(ValueError):
        strategy.generate(["AAPL"], view("AAPL", [100.0 - i for i in range(40)]))


# -- target weights --------------------------------------------------------


def test_target_weights_buys_when_underweight():
    strategy = build("target_weights", {"weights": {"SPY": 0.30}, "band": 0.05})
    signals = strategy.generate(["SPY"], view("SPY", [400.0], held=0))
    assert [s.side for s in signals] == ["buy"]
    assert signals[0].details["notional"] == pytest.approx(3000.0)


def test_target_weights_holds_inside_the_band():
    # 7 shares * $400 = $2,800 = 28% of $10k, within 5% of a 30% target.
    strategy = build("target_weights", {"weights": {"SPY": 0.30}, "band": 0.05})
    assert strategy.generate(["SPY"], view("SPY", [400.0], held=7)) == []


def test_target_weights_sells_when_overweight():
    strategy = build("target_weights", {"weights": {"SPY": 0.10}, "band": 0.05})
    signals = strategy.generate(["SPY"], view("SPY", [400.0], held=10))
    assert [s.side for s in signals] == ["sell"]


def test_target_weights_rejects_oversubscribed_book():
    strategy = build("target_weights", {"weights": {"SPY": 0.7, "QQQ": 0.5}})
    with pytest.raises(ValueError, match="more than the portfolio"):
        strategy.generate(["SPY", "QQQ"], view("SPY", [400.0]))


def test_target_weights_rejects_unweighted_symbol():
    strategy = build("target_weights", {"weights": {"SPY": 0.3}})
    with pytest.raises(ValueError, match="no target weight"):
        strategy.generate(["QQQ"], view("QQQ", [400.0]))


def test_unknown_strategy_name():
    with pytest.raises(ValueError, match="Unknown strategy"):
        build("moon_phase", {})
