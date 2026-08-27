"""RSI mean reversion.

Buy oversold, sell back when the bounce reaches the exit threshold. Only ever
sells what it already holds, so it cannot open a short.
"""

from __future__ import annotations

from ..indicators import rsi
from .base import BaseStrategy, MarketView, Signal


class RsiReversion(BaseStrategy):
    name = "rsi_reversion"

    def generate(self, symbols: list[str], view: MarketView) -> list[Signal]:
        period = self.param("period", 14)
        oversold = self.param("oversold", 30.0)
        overbought = self.param("overbought", 70.0)
        strength = self.param("strength", 1.0)

        if not 0 < oversold < overbought < 100:
            raise ValueError(
                f"{self.name}: need 0 < oversold ({oversold}) < overbought ({overbought}) < 100"
            )

        signals: list[Signal] = []
        for symbol in symbols:
            closes = view.closes.get(symbol) or []
            value = rsi(closes, period)
            if value is None:
                continue

            details = {"rsi": round(value, 2), "period": period}

            if value <= oversold and not view.holds(symbol):
                signals.append(
                    Signal(
                        symbol=symbol,
                        side="buy",
                        strength=strength,
                        reason=f"RSI({period}) {value:.1f} at or below oversold {oversold}",
                        strategy=self.name,
                        details=details,
                    )
                )
            elif value >= overbought and view.holds(symbol):
                signals.append(
                    Signal(
                        symbol=symbol,
                        side="sell",
                        strength=1.0,
                        reason=f"RSI({period}) {value:.1f} at or above overbought {overbought}",
                        strategy=self.name,
                        details=details,
                    )
                )
        return signals
