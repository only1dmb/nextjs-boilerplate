"""Order sizing and placement.

This is the only module that calls the broker's write endpoints, and the only
place the paper/confirm/live distinction is enforced. Sizing happens before the
risk stack runs, so guards see the exact order that would be sent.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Any, Callable

from .client import AccountSnapshot, BrokerError, RobinhoodClient
from .config import Config
from .journal import Journal
from .risk import RiskContext, TradeIntent, evaluate
from .strategies import Signal


@dataclass
class ExecutionResult:
    intent: TradeIntent
    placed: bool
    mode: str
    detail: str
    order_id: str | None = None


def size_intent(
    signal: Signal,
    snapshot: AccountSnapshot,
    config: Config,
    prices: dict[str, float],
) -> TradeIntent | None:
    """Turn a signal into a concrete, whole-share order.

    Returns None when the signal cannot be expressed as a legal order at all
    (nothing held to sell, or one share already costs more than the per-order
    cap). Those cases are not risk rejections, they are non-events.
    """
    price = prices.get(signal.symbol)
    if not price:
        held = snapshot.positions.get(signal.symbol)
        price = held.price if held else 0.0
    if not price:
        return None
    price = float(price)

    if signal.side == "sell":
        held = snapshot.positions.get(signal.symbol)
        if not held or held.quantity <= 0:
            return None
        quantity = math.floor(held.quantity * signal.strength)
        # Selling a sub-share remainder is still worth doing on a full exit.
        if quantity <= 0 and signal.strength >= 1.0:
            quantity = held.quantity
        if quantity <= 0:
            return None
        return TradeIntent(
            symbol=signal.symbol,
            side="sell",
            quantity=quantity,
            price=price,
            strategy=signal.strategy,
            reason=signal.reason,
        )

    # Buy: budget is the per-order cap scaled by signal strength, further
    # limited by concentration headroom and available cash. A strategy that
    # names its own notional (rebalancing) is capped by the same ceiling.
    limits = config.risk
    budget = limits.max_order_notional * signal.strength
    requested = signal.details.get("notional")
    if requested:
        budget = min(budget, float(requested))

    headroom = limits.max_position_pct * snapshot.equity - snapshot.position_value(
        signal.symbol
    )
    budget = min(budget, max(headroom, 0.0))
    budget = min(budget, max(snapshot.cash - limits.min_cash_buffer, 0.0))

    quantity = math.floor(budget / price)
    if quantity <= 0:
        return None

    return TradeIntent(
        symbol=signal.symbol,
        side="buy",
        quantity=quantity,
        price=price,
        strategy=signal.strategy,
        reason=signal.reason,
    )


def _limit_price(intent: TradeIntent, config: Config) -> float:
    """Rest the limit slightly through the quote so it fills but cannot chase."""
    slip = config.limit_slippage_pct
    if intent.side == "buy":
        return round(intent.price * (1 + slip), 2)
    return round(intent.price * (1 - slip), 2)


def _default_confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        # Unattended `confirm` mode must not auto-approve on a closed stdin.
        return False
    answer = input(prompt).strip().lower()
    return answer in ("y", "yes")


class Executor:
    def __init__(
        self,
        client: RobinhoodClient,
        config: Config,
        journal: Journal,
        confirm_fn: Callable[[str], bool] | None = None,
    ):
        self.client = client
        self.config = config
        self.journal = journal
        self.confirm_fn = confirm_fn or _default_confirm

    def execute(self, intent: TradeIntent, ctx: RiskContext) -> ExecutionResult:
        decision = evaluate(intent, ctx)

        self.journal.write(
            "risk_decision",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            price=intent.price,
            notional=round(intent.notional, 2),
            strategy=intent.strategy,
            reason=intent.reason,
            allowed=decision.allowed,
            rejections=decision.rejections,
        )

        if not decision.allowed:
            return ExecutionResult(intent, False, self.config.mode, decision.reason)

        if self.config.is_paper:
            self.journal.write(
                "order_submitted",
                simulated=True,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                price=intent.price,
                notional=round(intent.notional, 2),
                strategy=intent.strategy,
                reason=intent.reason,
            )
            return ExecutionResult(
                intent, True, "paper", "simulated fill (no order sent to Robinhood)"
            )

        # Second key for autonomous trading. Raises unless the environment
        # explicitly arms live mode.
        self.config.check_live_arm()

        if self.config.mode == "confirm":
            summary = (
                f"\n  {intent.side.upper()} {intent.quantity:g} {intent.symbol} "
                f"@ ~${intent.price:,.2f}  (${intent.notional:,.2f})\n"
                f"  why: {intent.reason}\n"
                f"  Place this REAL order? [y/N] "
            )
            if not self.confirm_fn(summary):
                self.journal.write(
                    "order_declined",
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=intent.quantity,
                    strategy=intent.strategy,
                )
                return ExecutionResult(intent, False, "confirm", "declined at the prompt")

        try:
            if self.config.order_type == "limit":
                limit = _limit_price(intent, self.config)
                order = self.client.submit_limit(
                    intent.symbol,
                    intent.side,
                    intent.quantity,
                    limit,
                    self.config.time_in_force,
                    self.config.allow_extended_hours,
                )
                placed_at: Any = limit
            else:
                order = self.client.submit_market(
                    intent.symbol,
                    intent.side,
                    intent.quantity,
                    self.config.time_in_force,
                    self.config.allow_extended_hours,
                )
                placed_at = "market"
        except BrokerError as exc:
            self.journal.write(
                "order_failed",
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                strategy=intent.strategy,
                error=str(exc),
            )
            return ExecutionResult(intent, False, self.config.mode, f"broker rejected: {exc}")

        order_id = order.get("id")
        self.journal.write(
            "order_submitted",
            simulated=False,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            price=intent.price,
            limit_price=placed_at,
            notional=round(intent.notional, 2),
            strategy=intent.strategy,
            reason=intent.reason,
            order_id=order_id,
            order_state=order.get("state"),
        )
        return ExecutionResult(
            intent,
            True,
            self.config.mode,
            f"submitted at {placed_at}",
            order_id=order_id,
        )
