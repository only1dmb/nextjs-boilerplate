"""Pre-trade risk guards.

Every proposed trade passes through this stack before it can reach the broker.
Guards only ever reject -- none of them can enlarge an order, relax a limit, or
approve something another guard blocked. A trade is allowed only when every
guard allows it.

The stack is deliberately boring and fully testable: no network calls happen
here, everything is derived from the context the engine hands in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .client import AccountSnapshot
from .config import Config


@dataclass(frozen=True)
class TradeIntent:
    symbol: str
    side: str
    quantity: float
    price: float
    strategy: str = ""
    reason: str = ""

    @property
    def notional(self) -> float:
        return self.quantity * self.price


@dataclass
class RiskContext:
    config: Config
    snapshot: AccountSnapshot
    halted: bool
    market_open: bool | None
    open_order_symbols: set[str]
    trades_today: int
    day_trade_count: int
    day_open_equity: float | None
    last_trade_at: dict[str, datetime] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def today(self) -> date:
        return self.now.date()


@dataclass
class RiskDecision:
    allowed: bool
    rejections: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(self.rejections) if self.rejections else "all guards passed"


# Each guard returns a rejection string, or None to allow.
def _guard_halt(intent: TradeIntent, ctx: RiskContext) -> str | None:
    if ctx.halted:
        return "agent is halted (state/HALT exists) -- run `resume` to clear"
    return None


def _guard_market_hours(intent: TradeIntent, ctx: RiskContext) -> str | None:
    if ctx.config.allow_extended_hours:
        return None
    if ctx.market_open is False:
        return "market is closed and allow_extended_hours is false"
    if ctx.market_open is None:
        return "could not confirm market hours -- refusing to trade blind"
    return None


def _guard_allowlist(intent: TradeIntent, ctx: RiskContext) -> str | None:
    if intent.symbol not in ctx.config.risk.symbol_allowlist:
        return f"{intent.symbol} is not in risk.symbol_allowlist"
    return None


def _guard_sane_intent(intent: TradeIntent, ctx: RiskContext) -> str | None:
    if intent.quantity <= 0:
        return f"quantity {intent.quantity} is not positive"
    if intent.price <= 0:
        return f"price {intent.price} is not positive"
    return None


def _guard_order_notional(intent: TradeIntent, ctx: RiskContext) -> str | None:
    limits = ctx.config.risk
    notional = intent.notional
    if notional < limits.min_order_notional:
        return (
            f"order notional ${notional:,.2f} is below min_order_notional "
            f"${limits.min_order_notional:,.2f}"
        )
    if notional > limits.max_order_notional:
        return (
            f"order notional ${notional:,.2f} exceeds max_order_notional "
            f"${limits.max_order_notional:,.2f}"
        )
    return None


def _guard_position_concentration(intent: TradeIntent, ctx: RiskContext) -> str | None:
    if intent.side != "buy":
        return None
    equity = ctx.snapshot.equity
    if equity <= 0:
        return "portfolio equity is zero or unknown"
    projected = ctx.snapshot.position_value(intent.symbol) + intent.notional
    projected_pct = projected / equity
    limit = ctx.config.risk.max_position_pct
    if projected_pct > limit:
        return (
            f"{intent.symbol} would reach {projected_pct:.1%} of the portfolio, "
            f"over max_position_pct {limit:.1%}"
        )
    return None


def _guard_cash_buffer(intent: TradeIntent, ctx: RiskContext) -> str | None:
    if intent.side != "buy":
        return None
    remaining = ctx.snapshot.cash - intent.notional
    buffer = ctx.config.risk.min_cash_buffer
    if remaining < buffer:
        return (
            f"buy would leave ${remaining:,.2f} cash, under min_cash_buffer "
            f"${buffer:,.2f}"
        )
    return None


def _guard_sell_only_what_is_held(intent: TradeIntent, ctx: RiskContext) -> str | None:
    if intent.side != "sell":
        return None
    held = ctx.snapshot.positions.get(intent.symbol)
    held_quantity = held.quantity if held else 0.0
    if intent.quantity > held_quantity + 1e-9:
        return (
            f"sell of {intent.quantity:g} exceeds the {held_quantity:g} held "
            f"-- the agent never opens shorts"
        )
    return None


def _guard_daily_trade_count(intent: TradeIntent, ctx: RiskContext) -> str | None:
    limit = ctx.config.risk.max_trades_per_day
    if ctx.trades_today >= limit:
        return f"already placed {ctx.trades_today} trades today (max_trades_per_day {limit})"
    return None


def _guard_pdt(intent: TradeIntent, ctx: RiskContext) -> str | None:
    limits = ctx.config.risk
    if ctx.snapshot.equity >= limits.pdt_equity_threshold:
        return None
    if ctx.day_trade_count >= limits.pdt_day_trade_limit:
        return (
            f"{ctx.day_trade_count} day trades on record and equity "
            f"${ctx.snapshot.equity:,.2f} is under the "
            f"${limits.pdt_equity_threshold:,.0f} PDT threshold"
        )
    return None


def _guard_daily_drawdown(intent: TradeIntent, ctx: RiskContext) -> str | None:
    if ctx.day_open_equity is None or ctx.day_open_equity <= 0:
        return None
    drop = (ctx.day_open_equity - ctx.snapshot.equity) / ctx.day_open_equity
    limit = ctx.config.risk.max_daily_drawdown_pct
    if drop >= limit:
        return (
            f"portfolio is down {drop:.2%} today, at or past the "
            f"{limit:.2%} circuit breaker"
        )
    return None


def _guard_duplicate_order(intent: TradeIntent, ctx: RiskContext) -> str | None:
    if intent.symbol in ctx.open_order_symbols:
        return f"an order for {intent.symbol} is already working at the broker"
    return None


def _guard_cooldown(intent: TradeIntent, ctx: RiskContext) -> str | None:
    cooldown = ctx.config.risk.per_symbol_cooldown_minutes
    if cooldown <= 0:
        return None
    last = ctx.last_trade_at.get(intent.symbol)
    if last is None:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed_minutes = (ctx.now - last).total_seconds() / 60
    if elapsed_minutes < cooldown:
        return (
            f"{intent.symbol} traded {elapsed_minutes:.0f}m ago, inside the "
            f"{cooldown}m cooldown"
        )
    return None


GUARDS = (
    _guard_halt,
    _guard_sane_intent,
    _guard_market_hours,
    _guard_allowlist,
    _guard_order_notional,
    _guard_sell_only_what_is_held,
    _guard_position_concentration,
    _guard_cash_buffer,
    _guard_daily_trade_count,
    _guard_pdt,
    _guard_daily_drawdown,
    _guard_duplicate_order,
    _guard_cooldown,
)


def evaluate(intent: TradeIntent, ctx: RiskContext) -> RiskDecision:
    """Run every guard and collect all rejections.

    All guards run even after the first rejection, so the journal records the
    complete reason a trade was blocked rather than whichever guard fired first.
    """
    rejections = [r for guard in GUARDS if (r := guard(intent, ctx)) is not None]
    return RiskDecision(allowed=not rejections, rejections=rejections)
