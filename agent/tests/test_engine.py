"""End-to-end cycles against a fake broker."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from robinhood_agent.client import AccountSnapshot, Position
from robinhood_agent.config import StrategyConfig
from robinhood_agent.engine import Engine
from robinhood_agent.journal import Journal


class FakeClient:
    def __init__(self, closes, positions=None, equity=10_000.0, cash=5_000.0):
        self._closes = closes
        self._positions = positions or {}
        self.equity = equity
        self.cash = cash
        self.orders = []
        self.market_open = True

    def snapshot(self):
        return AccountSnapshot(
            equity=self.equity,
            cash=self.cash,
            buying_power=self.cash,
            positions=dict(self._positions),
            taken_at=datetime.now(timezone.utc),
        )

    def latest_prices(self, symbols):
        return {s: self._closes[s][-1] for s in symbols if s in self._closes}

    def historical_closes(self, symbol, interval="day", span="year"):
        return list(self._closes.get(symbol, []))

    def open_order_symbols(self):
        return set()

    def recent_day_trade_count(self):
        return 0

    def market_is_open(self):
        return self.market_open

    def submit_limit(self, symbol, side, quantity, limit_price, tif, ext):
        self.orders.append((symbol, side, quantity))
        return {"id": f"o{len(self.orders)}", "state": "queued"}

    def submit_market(self, symbol, side, quantity, tif, ext):
        return self.submit_limit(symbol, side, quantity, 0, tif, ext)


def make_engine(config, tmp_path, client):
    return Engine(client, config, Journal(tmp_path / "journal.jsonl"))


@pytest.fixture
def crossover_config(config):
    return replace(
        config,
        strategies=[
            StrategyConfig(
                name="sma_crossover", symbols=["AAPL"], params={"fast": 5, "slow": 20}
            )
        ],
    )


def golden_cross_series():
    """Flat, then a sharp rally that pulls the fast SMA through the slow one."""
    return [100.0] * 60 + [100.0 + i * 3 for i in range(1, 12)]


def test_paper_cycle_places_no_real_order(crossover_config, tmp_path):
    client = FakeClient({"AAPL": golden_cross_series()})
    engine = make_engine(crossover_config, tmp_path, client)

    report = engine.run_cycle(execute=True)

    assert report.error is None
    assert client.orders == []  # paper mode
    if report.placed:
        assert all(r.mode == "paper" for r in report.placed)


def test_plan_never_executes(crossover_config, tmp_path):
    client = FakeClient({"AAPL": golden_cross_series()})
    engine = make_engine(crossover_config, tmp_path, client)

    report = engine.run_cycle(execute=False)

    assert client.orders == []
    assert all(not r.placed for r in report.results)


def test_halt_short_circuits_the_cycle(crossover_config, tmp_path):
    client = FakeClient({"AAPL": golden_cross_series()})
    engine = make_engine(crossover_config, tmp_path, client)
    engine.halt("test")

    report = engine.run_cycle(execute=True)

    assert "halted" in report.error
    assert report.signals == []
    assert client.orders == []


def test_resume_clears_the_halt(crossover_config, tmp_path):
    engine = make_engine(crossover_config, tmp_path, FakeClient({}))
    engine.halt("test")
    assert engine.is_halted()
    assert engine.resume()
    assert not engine.is_halted()
    assert not engine.resume()  # already clear


def test_drawdown_trips_the_breaker_and_halts(crossover_config, tmp_path):
    client = FakeClient({"AAPL": golden_cross_series()})
    engine = make_engine(crossover_config, tmp_path, client)

    # First cycle records the day's opening equity.
    engine.run_cycle(execute=True)
    client.equity = 9_500.0  # down 5%, past the 3% breaker

    report = engine.run_cycle(execute=True)

    assert "circuit breaker" in report.error
    assert engine.is_halted()


def test_breaker_trips_even_when_no_strategy_fires(config, tmp_path):
    """Regression: the breaker used to sit behind the `no signals` early exit.

    A flat tape produces no signals, so a portfolio bleeding out on positions
    the agent already held would never have been checked.
    """
    flat = {"AAPL": [100.0] * 80}
    client = FakeClient(flat)
    engine = make_engine(
        replace(
            config,
            strategies=[
                StrategyConfig("sma_crossover", ["AAPL"], {"fast": 5, "slow": 20})
            ],
        ),
        tmp_path,
        client,
    )

    first = engine.run_cycle(execute=True)
    assert first.signals == []  # nothing to trade on a flat tape

    client.equity = 9_000.0  # down 10% while the agent was idle
    report = engine.run_cycle(execute=True)

    assert "circuit breaker" in report.error
    assert engine.is_halted()


def test_sells_win_when_strategies_disagree(config, tmp_path):
    """A buy and a sell on the same symbol resolve to the sell."""
    from robinhood_agent.strategies import Signal

    client = FakeClient({"AAPL": [100.0] * 30})
    engine = make_engine(config, tmp_path, client)

    resolved = engine._resolve_conflicts(
        [
            Signal("AAPL", "buy", 1.0, "strategy A says buy", strategy="a"),
            Signal("AAPL", "sell", 1.0, "strategy B says sell", strategy="b"),
        ]
    )
    assert len(resolved) == 1
    assert resolved[0].side == "sell"


def test_a_broken_strategy_does_not_kill_the_cycle(config, tmp_path):
    bad = StrategyConfig(
        name="sma_crossover", symbols=["AAPL"], params={"fast": 50, "slow": 5}
    )
    client = FakeClient({"AAPL": golden_cross_series()})
    engine = make_engine(replace(config, strategies=[bad]), tmp_path, client)

    report = engine.run_cycle(execute=True)

    assert report.error is None
    assert report.signals == []
    assert any(r["event"] == "strategy_failed" for r in engine.journal.read())


def test_closed_market_blocks_execution(crossover_config, tmp_path):
    client = FakeClient({"AAPL": golden_cross_series()})
    client.market_open = False
    engine = make_engine(crossover_config, tmp_path, client)

    report = engine.run_cycle(execute=True)

    assert client.orders == []
    assert all(not r.placed for r in report.results)


def test_journal_records_every_decision(crossover_config, tmp_path):
    client = FakeClient({"AAPL": golden_cross_series()})
    engine = make_engine(crossover_config, tmp_path, client)
    engine.run_cycle(execute=True)

    events = {r["event"] for r in engine.journal.read()}
    assert "snapshot" in events
    assert "cycle_complete" in events


def test_positions_feed_back_into_strategies(config, tmp_path):
    """Holding a symbol suppresses a second buy from the same strategy."""
    positions = {"AAPL": Position("AAPL", 10, 100.0, 130.0)}
    client = FakeClient({"AAPL": golden_cross_series()}, positions=positions)
    engine = make_engine(
        replace(
            config,
            strategies=[
                StrategyConfig("sma_crossover", ["AAPL"], {"fast": 5, "slow": 20})
            ],
        ),
        tmp_path,
        client,
    )

    report = engine.run_cycle(execute=True)
    assert all(s.side != "buy" for s in report.signals)
