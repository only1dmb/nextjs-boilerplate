"""The agent loop.

One cycle: read the account, read the market, ask every strategy for signals,
size them, run each through the risk stack, execute what survives. Every step
is journalled.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .client import AccountSnapshot, RobinhoodClient
from .config import Config
from .executor import ExecutionResult, Executor, size_intent
from .journal import Journal
from .risk import RiskContext
from .strategies import MarketView, Signal, build


@dataclass
class CycleReport:
    started_at: datetime
    snapshot: AccountSnapshot | None = None
    signals: list[Signal] = field(default_factory=list)
    results: list[ExecutionResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def placed(self) -> list[ExecutionResult]:
        return [r for r in self.results if r.placed]


class Engine:
    def __init__(self, client: RobinhoodClient, config: Config, journal: Journal,
                 executor: Executor | None = None):
        self.client = client
        self.config = config
        self.journal = journal
        self.executor = executor or Executor(client, config, journal)
        self._strategies = [
            (spec, build(spec.name, spec.params))
            for spec in config.strategies
            if spec.enabled
        ]

    # -- state -------------------------------------------------------------

    def is_halted(self) -> bool:
        return self.config.halt_file.exists()

    def halt(self, reason: str) -> None:
        self.config.halt_file.parent.mkdir(parents=True, exist_ok=True)
        self.config.halt_file.write_text(
            f"{datetime.now(timezone.utc).isoformat()}\n{reason}\n"
        )
        self.journal.write("halted", reason=reason)

    def resume(self) -> bool:
        if self.config.halt_file.exists():
            self.config.halt_file.unlink()
            self.journal.write("resumed")
            return True
        return False

    # -- one cycle ---------------------------------------------------------

    def build_market_view(
        self, symbols: list[str], snapshot: AccountSnapshot
    ) -> tuple[MarketView, dict[str, float]]:
        prices = self.client.latest_prices(symbols)
        closes: dict[str, list[float]] = {}
        for symbol in symbols:
            try:
                closes[symbol] = self.client.historical_closes(symbol)
            except Exception as exc:  # one bad symbol must not kill the cycle
                self.journal.write("history_failed", symbol=symbol, error=str(exc))
                closes[symbol] = []
            # The live bar is not in `historicals` until the session closes.
            if symbol in prices and closes[symbol]:
                closes[symbol] = closes[symbol] + [prices[symbol]]

        view = MarketView(
            prices=prices,
            closes=closes,
            held_quantity={s: p.quantity for s, p in snapshot.positions.items()},
            held_value={s: p.market_value for s, p in snapshot.positions.items()},
            equity=snapshot.equity,
        )
        return view, prices

    def collect_signals(self, view: MarketView) -> list[Signal]:
        signals: list[Signal] = []
        for spec, strategy in self._strategies:
            try:
                produced = strategy.generate(list(spec.symbols), view)
            except Exception as exc:
                self.journal.write("strategy_failed", strategy=spec.name, error=str(exc))
                continue
            for signal in produced:
                self.journal.write(
                    "signal",
                    strategy=signal.strategy or spec.name,
                    symbol=signal.symbol,
                    side=signal.side,
                    strength=signal.strength,
                    reason=signal.reason,
                    details=signal.details,
                )
            signals.extend(produced)
        return self._resolve_conflicts(signals)

    def _resolve_conflicts(self, signals: list[Signal]) -> list[Signal]:
        """One signal per symbol per cycle, and sells win.

        Two strategies disagreeing about a symbol would otherwise round-trip
        the position and pay the spread twice. When they disagree the agent
        takes the risk-reducing side.
        """
        by_symbol: dict[str, Signal] = {}
        for signal in signals:
            existing = by_symbol.get(signal.symbol)
            if existing is None:
                by_symbol[signal.symbol] = signal
                continue
            if existing.side == signal.side:
                continue
            winner = existing if existing.side == "sell" else signal
            self.journal.write(
                "signal_conflict",
                symbol=signal.symbol,
                kept=f"{winner.strategy}:{winner.side}",
                dropped=(
                    f"{signal.strategy}:{signal.side}"
                    if winner is existing
                    else f"{existing.strategy}:{existing.side}"
                ),
            )
            by_symbol[signal.symbol] = winner
        return list(by_symbol.values())

    def build_risk_context(self, snapshot: AccountSnapshot) -> RiskContext:
        now = datetime.now(timezone.utc)
        today = now.date()
        symbols = self.config.traded_symbols()

        last_trade_at = {}
        for symbol in symbols:
            stamp = self.journal.last_trade_time(symbol)
            if stamp is not None:
                last_trade_at[symbol] = stamp

        return RiskContext(
            config=self.config,
            snapshot=snapshot,
            halted=self.is_halted(),
            market_open=self.client.market_is_open(),
            open_order_symbols=self.client.open_order_symbols(),
            trades_today=len(self.journal.filled_orders_on(today)),
            day_trade_count=self.client.recent_day_trade_count(),
            day_open_equity=self.journal.day_open_equity(today),
            last_trade_at=last_trade_at,
            now=now,
        )

    def _check_circuit_breaker(self, snapshot: AccountSnapshot) -> bool:
        """Halt the agent for the day if equity has fallen far enough.

        A tripped breaker halts rather than blocking a single trade: if the
        book is down this much, the day is over regardless of what the next
        signal says.
        """
        day_open = self.journal.day_open_equity(datetime.now(timezone.utc).date())
        if not day_open or day_open <= 0:
            return False
        drop = (day_open - snapshot.equity) / day_open
        limit = self.config.risk.max_daily_drawdown_pct
        if drop < limit:
            return False
        self.halt(f"daily drawdown {drop:.2%} hit the {limit:.2%} circuit breaker")
        return True

    def run_cycle(self, execute: bool = True) -> CycleReport:
        report = CycleReport(started_at=datetime.now(timezone.utc))

        if self.is_halted():
            report.error = "agent is halted -- run `resume` to clear"
            self.journal.write("cycle_skipped", reason=report.error)
            return report

        try:
            snapshot = self.client.snapshot()
        except Exception as exc:
            report.error = f"could not read account: {exc}"
            self.journal.write("cycle_failed", error=str(exc))
            return report

        report.snapshot = snapshot
        # Written before anything else so the first cycle of the day sets the
        # baseline the drawdown circuit breaker measures against.
        self.journal.write(
            "snapshot",
            equity=round(snapshot.equity, 2),
            cash=round(snapshot.cash, 2),
            positions={s: round(p.quantity, 6) for s, p in snapshot.positions.items()},
        )

        # The circuit breaker is checked before anything else that could
        # produce a trade, and independently of whether strategies fire this
        # cycle -- a quiet cycle during a bad day must still stop the agent.
        if self._check_circuit_breaker(snapshot):
            report.error = "circuit breaker tripped -- agent halted"
            return report

        symbols = self.config.traded_symbols()
        if not symbols:
            report.error = "no enabled strategies with symbols"
            return report

        view, prices = self.build_market_view(symbols, snapshot)
        report.signals = self.collect_signals(view)

        if not report.signals:
            self.journal.write("cycle_complete", signals=0, orders=0)
            return report

        ctx = self.build_risk_context(snapshot)

        for signal in report.signals:
            intent = size_intent(signal, snapshot, self.config, prices)
            if intent is None:
                note = (
                    f"{signal.side} {signal.symbol}: no legal order size "
                    f"(cash, position, or per-order cap)"
                )
                report.skipped.append(note)
                self.journal.write(
                    "signal_unsized",
                    symbol=signal.symbol,
                    side=signal.side,
                    strategy=signal.strategy,
                )
                continue

            if not execute:
                from .risk import evaluate

                decision = evaluate(intent, ctx)
                report.results.append(
                    ExecutionResult(
                        intent,
                        False,
                        "plan",
                        "would place" if decision.allowed else decision.reason,
                    )
                )
                continue

            result = self.executor.execute(intent, ctx)
            report.results.append(result)

            # Keep the in-cycle view honest: a filled buy consumes cash and
            # counts against the daily trade budget for the next signal.
            if result.placed:
                ctx.trades_today += 1
                ctx.last_trade_at[intent.symbol] = ctx.now
                if intent.side == "buy":
                    snapshot.cash -= intent.notional

        self.journal.write(
            "cycle_complete",
            signals=len(report.signals),
            orders=len(report.placed),
        )
        return report

    def run_forever(self, max_cycles: int | None = None) -> None:
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            report = self.run_cycle(execute=True)
            if report.error:
                print(f"[{report.started_at:%H:%M:%S}] {report.error}")
            else:
                print(
                    f"[{report.started_at:%H:%M:%S}] "
                    f"{len(report.signals)} signal(s), {len(report.placed)} order(s)"
                )
            if self.is_halted():
                print("Agent halted. Stopping loop.")
                return
            cycles += 1
            if max_cycles is None or cycles < max_cycles:
                time.sleep(self.config.poll_interval_seconds)
