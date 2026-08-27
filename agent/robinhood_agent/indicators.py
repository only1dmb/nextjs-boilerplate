"""Pure-python technical indicators.

No numpy/pandas dependency: the series here are a few hundred daily bars, and
keeping them plain makes the backtester and the live path share exact code.
Every function takes closes oldest-first and returns None when there is not
enough history to compute an honest value.
"""

from __future__ import annotations


def sma(closes: list[float], window: int) -> float | None:
    if window <= 0 or len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def sma_series(closes: list[float], window: int) -> list[float | None]:
    """SMA at each index, None until the window fills."""
    out: list[float | None] = []
    running = 0.0
    for index, close in enumerate(closes):
        running += close
        if index >= window:
            running -= closes[index - window]
        out.append(running / window if index >= window - 1 else None)
    return out


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI."""
    if period <= 0 or len(closes) < period + 1:
        return None

    gains = 0.0
    losses = 0.0
    for previous, current in zip(closes[:period], closes[1 : period + 1]):
        change = current - previous
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period

    # Wilder smoothing over the remainder of the series.
    for previous, current in zip(closes[period:-1], closes[period + 1 :]):
        change = current - previous
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def pct_change(closes: list[float], lookback: int) -> float | None:
    if lookback <= 0 or len(closes) < lookback + 1:
        return None
    past = closes[-(lookback + 1)]
    if past == 0:
        return None
    return (closes[-1] - past) / past
