"""Replay strategies over historical bars.

Deliberately pessimistic: fills happen at the next bar's close, not the signal
bar's, and every fill pays the configured slippage. A strategy that only looks
good without those two things is not a strategy.

This shares the exact Strategy and MarketView types the live engine uses, so
what you test is what runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config
from .strategies import MarketView, build


@dataclass
class Fill:
    bar: int
    symbol: str
    side: str
    quantity: float
    price: float
    reason: str


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    bars: int
    start_equity: float
    end_equity: float
    buy_and_hold_equity: float
    fills: list[Fill] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    @property
    def total_return(self) -> float:
        if self.start_equity <= 0:
            return 0.0
        return (self.end_equity - self.start_equity) / self.start_equity

    @property
    def buy_and_hold_return(self) -> float:
        if self.start_equity <= 0:
            return 0.0
        return (self.buy_and_hold_equity - self.start_equity) / self.start_equity

    @property
    def max_drawdown(self) -> float:
        peak = float("-inf")
        worst = 0.0
        for value in self.equity_curve:
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return worst

    @property
    def trades(self) -> int:
        return len(self.fills)


def run_backtest(
    symbol: str,
    closes: list[float],
    strategy_name: str,
    params: dict,
    config: Config,
    start_equity: float = 10_000.0,
    warmup: int = 60,
) -> BacktestResult:
    strategy = build(strategy_name, params)
    slippage = config.limit_slippage_pct
    per_order_cap = config.risk.max_order_notional
    max_position_pct = config.risk.max_position_pct

    cash = start_equity
    quantity = 0.0
    fills: list[Fill] = []
    curve: list[float] = []
    pending: tuple[str, float, str] | None = None  # (side, strength, reason)

    for index in range(len(closes)):
        price = closes[index]
        equity = cash + quantity * price
        curve.append(equity)

        # Fill whatever last bar decided, at this bar's close.
        if pending is not None:
            side, strength, reason = pending
            pending = None
            if side == "buy":
                fill_price = price * (1 + slippage)
                budget = min(
                    per_order_cap * strength,
                    max(max_position_pct * equity - quantity * price, 0.0),
                    cash,
                )
                size = int(budget // fill_price)
                if size > 0:
                    cash -= size * fill_price
                    quantity += size
                    fills.append(Fill(index, symbol, "buy", size, fill_price, reason))
            elif quantity > 0:
                fill_price = price * (1 - slippage)
                cash += quantity * fill_price
                fills.append(Fill(index, symbol, "sell", quantity, fill_price, reason))
                quantity = 0.0

        if index < warmup:
            continue

        view = MarketView(
            prices={symbol: price},
            closes={symbol: closes[: index + 1]},
            held_quantity={symbol: quantity} if quantity > 0 else {},
            held_value={symbol: quantity * price} if quantity > 0 else {},
            equity=equity,
        )
        signals = strategy.generate([symbol], view)
        if signals:
            signal = signals[0]
            pending = (signal.side, signal.strength, signal.reason)

    final_price = closes[-1] if closes else 0.0
    end_equity = cash + quantity * final_price

    # Buy-and-hold benchmark, sized by the same concentration cap so the
    # comparison is against a strategy the agent would actually be allowed to run.
    first_price = closes[warmup] if len(closes) > warmup else (closes[0] if closes else 0.0)
    if first_price > 0:
        hold_size = int((max_position_pct * start_equity) // first_price)
        hold_equity = start_equity - hold_size * first_price + hold_size * final_price
    else:
        hold_equity = start_equity

    return BacktestResult(
        symbol=symbol,
        strategy=strategy_name,
        bars=len(closes),
        start_equity=start_equity,
        end_equity=end_equity,
        buy_and_hold_equity=hold_equity,
        fills=fills,
        equity_curve=curve,
    )
