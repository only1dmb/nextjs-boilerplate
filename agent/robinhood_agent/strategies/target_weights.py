"""Rebalance toward fixed target weights.

The least clever strategy here and the one most people actually want: hold
each symbol at a target share of the portfolio, and trade only when drift
exceeds a band. The band is what keeps this from churning on every cycle.
"""

from __future__ import annotations

from .base import BaseStrategy, MarketView, Signal


class TargetWeights(BaseStrategy):
    name = "target_weights"

    def generate(self, symbols: list[str], view: MarketView) -> list[Signal]:
        weights = self.param("weights", {}) or {}
        band = self.param("band", 0.05)

        weights = {str(k).upper(): float(v) for k, v in weights.items()}
        missing = [s for s in symbols if s not in weights]
        if missing:
            raise ValueError(f"{self.name}: no target weight for {missing}")

        total = sum(weights.values())
        if total > 1.0 + 1e-9:
            raise ValueError(
                f"{self.name}: weights sum to {total:.3f}, which is more than the portfolio"
            )
        if view.equity <= 0:
            return []

        signals: list[Signal] = []
        for symbol in symbols:
            target_pct = weights[symbol]
            current_value = view.held_value.get(symbol, 0.0)
            current_pct = current_value / view.equity
            drift = current_pct - target_pct

            if abs(drift) <= band:
                continue

            target_value = target_pct * view.equity
            trade_value = abs(target_value - current_value)
            details = {
                "target_pct": round(target_pct, 4),
                "current_pct": round(current_pct, 4),
                "drift": round(drift, 4),
                "band": band,
                "trade_value": round(trade_value, 2),
            }

            if drift < 0:
                signals.append(
                    Signal(
                        symbol=symbol,
                        side="buy",
                        strength=1.0,
                        reason=(
                            f"underweight {current_pct:.1%} vs target {target_pct:.1%} "
                            f"(band {band:.1%})"
                        ),
                        strategy=self.name,
                        details={**details, "notional": round(trade_value, 2)},
                    )
                )
            elif view.holds(symbol):
                signals.append(
                    Signal(
                        symbol=symbol,
                        side="sell",
                        strength=1.0,
                        reason=(
                            f"overweight {current_pct:.1%} vs target {target_pct:.1%} "
                            f"(band {band:.1%})"
                        ),
                        strategy=self.name,
                        details={**details, "notional": round(trade_value, 2)},
                    )
                )
        return signals
