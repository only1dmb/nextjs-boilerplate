"""Strategy interface.

A strategy is a pure function of market data and current holdings. It proposes
signals; it never places orders, never reads config outside its own params,
and never knows what mode the agent is in. Risk and execution happen
downstream, so a buggy strategy can overtrade in intent but not in effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: str  # "buy" or "sell"
    # Fraction of the strategy's allowed budget to deploy, in (0, 1].
    strength: float
    reason: str
    strategy: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if not 0 < self.strength <= 1:
            raise ValueError(f"strength must be in (0, 1], got {self.strength}")


@dataclass
class MarketView:
    """Everything a strategy is allowed to see."""

    prices: dict[str, float]
    closes: dict[str, list[float]]
    held_quantity: dict[str, float]
    held_value: dict[str, float]
    equity: float

    def holds(self, symbol: str) -> bool:
        return self.held_quantity.get(symbol, 0.0) > 0


class Strategy(Protocol):
    name: str

    def generate(self, symbols: list[str], view: MarketView) -> list[Signal]:
        ...


class BaseStrategy:
    name = "base"

    def __init__(self, **params: Any):
        self.params = params

    def param(self, key: str, default: Any) -> Any:
        value = self.params.get(key, default)
        if value is None:
            return default
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, int) and not isinstance(value, bool):
            return int(value)
        if isinstance(default, float):
            return float(value)
        return value

    def generate(self, symbols: list[str], view: MarketView) -> list[Signal]:
        raise NotImplementedError
