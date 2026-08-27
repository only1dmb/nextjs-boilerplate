"""Strategy registry."""

from __future__ import annotations

from typing import Any

from .base import BaseStrategy, MarketView, Signal, Strategy
from .rsi_reversion import RsiReversion
from .sma_crossover import SmaCrossover
from .target_weights import TargetWeights

REGISTRY: dict[str, type[BaseStrategy]] = {
    SmaCrossover.name: SmaCrossover,
    RsiReversion.name: RsiReversion,
    TargetWeights.name: TargetWeights,
}

__all__ = [
    "REGISTRY",
    "BaseStrategy",
    "MarketView",
    "RsiReversion",
    "Signal",
    "SmaCrossover",
    "Strategy",
    "TargetWeights",
    "build",
]


def build(name: str, params: dict[str, Any] | None = None) -> BaseStrategy:
    if name not in REGISTRY:
        raise ValueError(
            f"Unknown strategy {name!r}. Available: {sorted(REGISTRY)}"
        )
    return REGISTRY[name](**(params or {}))
