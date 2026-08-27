"""Moving-average crossover.

Buy when the fast SMA crosses above the slow SMA, sell the position back when
it crosses below. Trades the crossing, not the state: without the previous
bar's comparison this would re-fire a buy every cycle the fast line sits above
the slow one.
"""

from __future__ import annotations

from ..indicators import sma_series
from .base import BaseStrategy, MarketView, Signal


class SmaCrossover(BaseStrategy):
    name = "sma_crossover"

    def generate(self, symbols: list[str], view: MarketView) -> list[Signal]:
        fast_window = self.param("fast", 20)
        slow_window = self.param("slow", 50)
        strength = self.param("strength", 1.0)

        if fast_window >= slow_window:
            raise ValueError(
                f"{self.name}: fast ({fast_window}) must be less than slow ({slow_window})"
            )

        signals: list[Signal] = []
        for symbol in symbols:
            closes = view.closes.get(symbol) or []
            if len(closes) < slow_window + 1:
                continue

            fast = sma_series(closes, fast_window)
            slow = sma_series(closes, slow_window)
            if fast[-1] is None or slow[-1] is None or fast[-2] is None or slow[-2] is None:
                continue

            was_above = fast[-2] > slow[-2]
            is_above = fast[-1] > slow[-1]
            details = {
                "fast": round(fast[-1], 4),
                "slow": round(slow[-1], 4),
                "fast_window": fast_window,
                "slow_window": slow_window,
            }

            if is_above and not was_above and not view.holds(symbol):
                signals.append(
                    Signal(
                        symbol=symbol,
                        side="buy",
                        strength=strength,
                        reason=(
                            f"SMA{fast_window} crossed above SMA{slow_window} "
                            f"({fast[-1]:.2f} > {slow[-1]:.2f})"
                        ),
                        strategy=self.name,
                        details=details,
                    )
                )
            elif not is_above and was_above and view.holds(symbol):
                signals.append(
                    Signal(
                        symbol=symbol,
                        side="sell",
                        strength=1.0,  # exits are always the whole position
                        reason=(
                            f"SMA{fast_window} crossed below SMA{slow_window} "
                            f"({fast[-1]:.2f} < {slow[-1]:.2f})"
                        ),
                        strategy=self.name,
                        details=details,
                    )
                )
        return signals
