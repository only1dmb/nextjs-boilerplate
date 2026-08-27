"""Thin wrapper over robin_stocks.

Two jobs: keep every Robinhood call in one place so the rest of the agent is
testable without a network, and keep reads strictly separate from writes.
Only `executor.py` is allowed to reach the write methods.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NYSE_MIC = "XNYS"


class BrokerError(RuntimeError):
    """A Robinhood call failed or returned something unusable."""


@dataclass
class Position:
    symbol: str
    quantity: float
    average_buy_price: float
    price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.price

    @property
    def unrealized_pl(self) -> float:
        return (self.price - self.average_buy_price) * self.quantity


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float
    positions: dict[str, Position]
    taken_at: datetime

    def position_value(self, symbol: str) -> float:
        position = self.positions.get(symbol)
        return position.market_value if position else 0.0


class RobinhoodClient:
    """Authenticated Robinhood session.

    Credentials come from the environment only -- never from the config file,
    which is meant to be committable.
    """

    def __init__(self, session_path: Path | None = None):
        self._session_path = session_path
        self._logged_in = False
        self._rh: Any = None

    # -- session -----------------------------------------------------------

    def login(self, mfa_code: str | None = None) -> None:
        import robin_stocks.robinhood as rh

        self._rh = rh

        username = os.environ.get("ROBINHOOD_USERNAME")
        password = os.environ.get("ROBINHOOD_PASSWORD")
        if not username or not password:
            raise BrokerError(
                "Set ROBINHOOD_USERNAME and ROBINHOOD_PASSWORD in the environment "
                "(see agent/.env.example). They are never read from the config file."
            )

        mfa_code = mfa_code or os.environ.get("ROBINHOOD_MFA_CODE") or None
        totp_secret = os.environ.get("ROBINHOOD_TOTP_SECRET")
        if not mfa_code and totp_secret:
            mfa_code = self._totp(totp_secret)

        kwargs: dict[str, Any] = {"store_session": True}
        if self._session_path is not None:
            self._session_path.parent.mkdir(parents=True, exist_ok=True)
            kwargs["pickle_path"] = str(self._session_path.parent) + os.sep
            kwargs["pickle_name"] = self._session_path.stem
        if mfa_code:
            kwargs["mfa_code"] = mfa_code

        try:
            rh.login(username, password, **kwargs)
        except Exception as exc:  # robin_stocks raises bare exceptions
            raise BrokerError(f"Robinhood login failed: {exc}") from exc

        self._logged_in = True

    @staticmethod
    def _totp(secret: str) -> str:
        try:
            import pyotp
        except ImportError as exc:
            raise BrokerError(
                "ROBINHOOD_TOTP_SECRET is set but pyotp is not installed. "
                "Run: pip install -r agent/requirements.txt"
            ) from exc
        return pyotp.TOTP(secret).now()

    def logout(self) -> None:
        if self._logged_in and self._rh is not None:
            try:
                self._rh.logout()
            except Exception:
                pass
        self._logged_in = False

    @property
    def rh(self) -> Any:
        if not self._logged_in or self._rh is None:
            raise BrokerError("Not logged in -- call login() first")
        return self._rh

    # -- reads -------------------------------------------------------------

    def snapshot(self) -> AccountSnapshot:
        """Current equity, cash, and open stock positions."""
        account = self.rh.profiles.load_account_profile()
        portfolio = self.rh.profiles.load_portfolio_profile()
        if not account or not portfolio:
            raise BrokerError("Robinhood returned an empty account profile")

        equity = _to_float(portfolio.get("extended_hours_equity")) or _to_float(
            portfolio.get("equity")
        )
        if equity is None:
            raise BrokerError("Could not read portfolio equity")

        cash = _to_float(account.get("cash")) or 0.0
        buying_power = _to_float(account.get("buying_power")) or cash

        positions: dict[str, Position] = {}
        for symbol, holding in (self.rh.account.build_holdings() or {}).items():
            quantity = _to_float(holding.get("quantity")) or 0.0
            if quantity <= 0:
                continue
            positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                average_buy_price=_to_float(holding.get("average_buy_price")) or 0.0,
                price=_to_float(holding.get("price")) or 0.0,
            )

        return AccountSnapshot(
            equity=equity,
            cash=cash,
            buying_power=buying_power,
            positions=positions,
            taken_at=datetime.now(timezone.utc),
        )

    def latest_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        raw = self.rh.stocks.get_latest_price(symbols, includeExtendedHours=False)
        prices: dict[str, float] = {}
        for symbol, value in zip(symbols, raw or []):
            price = _to_float(value)
            if price:
                prices[symbol] = price
        return prices

    def historical_closes(
        self, symbol: str, interval: str = "day", span: str = "year"
    ) -> list[float]:
        """Closing prices, oldest first."""
        bars = self.rh.stocks.get_stock_historicals(
            symbol, interval=interval, span=span, bounds="regular"
        )
        closes = []
        for bar in bars or []:
            if not bar:
                continue
            close = _to_float(bar.get("close_price"))
            if close:
                closes.append(close)
        return closes

    def open_orders(self) -> list[dict[str, Any]]:
        return [o for o in (self.rh.orders.get_all_open_stock_orders() or []) if o]

    def open_order_symbols(self) -> set[str]:
        """Symbols with an order still working at the broker."""
        symbols = set()
        for order in self.open_orders():
            url = order.get("instrument")
            if not url:
                continue
            try:
                instrument = self.rh.stocks.get_instrument_by_url(url)
            except Exception:
                continue
            if instrument and instrument.get("symbol"):
                symbols.add(instrument["symbol"])
        return symbols

    def recent_day_trade_count(self) -> int:
        """Day trades on record in the current rolling window.

        Returns 0 if Robinhood does not answer -- the PDT guard treats an
        unavailable count as "unknown" and the caller decides what to do.
        """
        try:
            trades = self.rh.account.get_day_trades()
        except Exception:
            return 0
        if isinstance(trades, dict):
            for key in ("equity_day_trades", "day_trade_count"):
                count = trades.get(key)
                if isinstance(count, list):
                    return len(count)
                if count is not None:
                    try:
                        return int(count)
                    except (TypeError, ValueError):
                        continue
            return 0
        if isinstance(trades, list):
            return len([t for t in trades if t])
        return 0

    def market_is_open(self) -> bool | None:
        """True/False if known, None if Robinhood did not answer."""
        try:
            hours = self.rh.markets.get_market_today_hours(NYSE_MIC)
        except Exception:
            return None
        if not hours or not hours.get("is_open"):
            return False
        opens_at = _parse_time(hours.get("opens_at"))
        closes_at = _parse_time(hours.get("closes_at"))
        if opens_at is None or closes_at is None:
            return None
        return opens_at <= datetime.now(timezone.utc) <= closes_at

    # -- writes (executor only) --------------------------------------------

    def submit_limit(
        self,
        symbol: str,
        side: str,
        quantity: float,
        limit_price: float,
        time_in_force: str,
        extended_hours: bool,
    ) -> dict[str, Any]:
        fn = (
            self.rh.orders.order_buy_limit
            if side == "buy"
            else self.rh.orders.order_sell_limit
        )
        return _check_order(
            fn(
                symbol,
                quantity,
                round(limit_price, 2),
                timeInForce=time_in_force,
                extendedHours=extended_hours,
            )
        )

    def submit_market(
        self,
        symbol: str,
        side: str,
        quantity: float,
        time_in_force: str,
        extended_hours: bool,
    ) -> dict[str, Any]:
        fn = (
            self.rh.orders.order_buy_market
            if side == "buy"
            else self.rh.orders.order_sell_market
        )
        return _check_order(
            fn(
                symbol,
                quantity,
                timeInForce=time_in_force,
                extendedHours=extended_hours,
            )
        )

    def cancel_all_open_orders(self) -> Any:
        return self.rh.orders.cancel_all_stock_orders()


def _check_order(result: Any) -> dict[str, Any]:
    """robin_stocks returns the error body rather than raising."""
    if not isinstance(result, dict) or not result.get("id"):
        raise BrokerError(f"Robinhood rejected the order: {result}")
    return result


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp
