"""Configuration loading and validation.

Everything the agent is allowed to do is declared here. Nothing downstream
widens these limits -- risk guards read this config and only ever narrow.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

# The two-key arm for autonomous live trading. `mode: live` in the config is
# one key; this environment variable is the other. Both are required.
LIVE_ARM_ENV = "RH_AGENT_ARM_LIVE"
LIVE_ARM_VALUE = "I-ACCEPT-THE-RISK"

MODES = ("paper", "confirm", "live")


class ConfigError(ValueError):
    """Raised when a config file is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class RiskLimits:
    # Per-order bounds, in dollars of notional.
    max_order_notional: float = 250.0
    min_order_notional: float = 1.0

    # No single symbol may exceed this share of total portfolio value after a buy.
    max_position_pct: float = 0.10

    # Never spend down past this much settled cash.
    min_cash_buffer: float = 100.0

    # Hard stop on activity per calendar day.
    max_trades_per_day: int = 5

    # Circuit breaker: halt the agent for the day once portfolio equity has
    # dropped this fraction below the day's opening equity.
    max_daily_drawdown_pct: float = 0.03

    # Pattern-day-trader protection. Accounts under $25k get 3 day trades per
    # rolling 5 business days; the agent stops at this count.
    pdt_day_trade_limit: int = 3
    pdt_equity_threshold: float = 25_000.0

    # Minutes to wait before trading the same symbol again.
    per_symbol_cooldown_minutes: int = 60

    # Only these symbols may ever be traded. Empty list means "nothing" --
    # there is deliberately no wildcard.
    symbol_allowlist: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.min_order_notional <= 0:
            raise ConfigError("risk.min_order_notional must be > 0")
        if self.max_order_notional < self.min_order_notional:
            raise ConfigError("risk.max_order_notional must be >= min_order_notional")
        if not 0 < self.max_position_pct <= 1:
            raise ConfigError("risk.max_position_pct must be in (0, 1]")
        if self.min_cash_buffer < 0:
            raise ConfigError("risk.min_cash_buffer must be >= 0")
        if self.max_trades_per_day < 0:
            raise ConfigError("risk.max_trades_per_day must be >= 0")
        if not 0 < self.max_daily_drawdown_pct <= 1:
            raise ConfigError("risk.max_daily_drawdown_pct must be in (0, 1]")


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    symbols: list[str]
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True)
class Config:
    mode: str = "paper"
    risk: RiskLimits = field(default_factory=RiskLimits)
    strategies: list[StrategyConfig] = field(default_factory=list)

    # Order placement.
    order_type: str = "limit"  # "limit" or "market"
    limit_slippage_pct: float = 0.002  # how far through the quote a limit rests
    time_in_force: str = "gfd"
    allow_extended_hours: bool = False

    # Engine loop.
    poll_interval_seconds: int = 300

    # Paths, resolved relative to the agent directory.
    state_dir: Path = field(default_factory=lambda: Path("state"))

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ConfigError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.order_type not in ("limit", "market"):
            raise ConfigError("order_type must be 'limit' or 'market'")
        if not 0 <= self.limit_slippage_pct < 0.5:
            raise ConfigError("limit_slippage_pct must be in [0, 0.5)")
        if self.poll_interval_seconds < 30:
            raise ConfigError("poll_interval_seconds must be >= 30 to stay well under rate limits")

    # -- derived -----------------------------------------------------------

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"

    @property
    def places_real_orders(self) -> bool:
        return self.mode in ("confirm", "live")

    @property
    def halt_file(self) -> Path:
        return self.state_dir / "HALT"

    @property
    def journal_file(self) -> Path:
        return self.state_dir / "journal.jsonl"

    @property
    def session_file(self) -> Path:
        return self.state_dir / "session.pickle"

    def traded_symbols(self) -> list[str]:
        """Every symbol any enabled strategy might act on."""
        out: list[str] = []
        for strategy in self.strategies:
            if not strategy.enabled:
                continue
            for symbol in strategy.symbols:
                if symbol not in out:
                    out.append(symbol)
        return out

    def check_live_arm(self) -> None:
        """Enforce the second key for autonomous live trading.

        Raises ConfigError unless the environment explicitly arms live mode.
        Called before any order is placed without human confirmation.
        """
        if self.mode != "live":
            return
        actual = os.environ.get(LIVE_ARM_ENV, "")
        if actual != LIVE_ARM_VALUE:
            raise ConfigError(
                f"mode is 'live' but {LIVE_ARM_ENV} is not set to {LIVE_ARM_VALUE!r}. "
                "Autonomous trading needs both keys. Set the variable in the shell "
                "that runs the agent, or drop back to mode 'confirm'."
            )


def _resolve_state_dir(raw: Any, base_dir: Path) -> Path:
    state_dir = Path(raw) if raw else Path("state")
    if not state_dir.is_absolute():
        state_dir = base_dir / state_dir
    return state_dir


def load_config(path: str | Path) -> Config:
    """Read and validate a YAML config file."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"No config at {path}. Copy agent/config.example.yaml and edit it."
        )

    with path.open() as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")

    known = {
        "mode", "risk", "strategies", "order_type", "limit_slippage_pct",
        "time_in_force", "allow_extended_hours", "poll_interval_seconds", "state_dir",
    }
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"Unknown config keys: {sorted(unknown)}")

    risk_raw = raw.get("risk") or {}
    if not isinstance(risk_raw, dict):
        raise ConfigError("risk must be a mapping")
    unknown_risk = set(risk_raw) - set(RiskLimits.__dataclass_fields__)
    if unknown_risk:
        raise ConfigError(f"Unknown risk keys: {sorted(unknown_risk)}")
    risk_raw = dict(risk_raw)
    risk_raw["symbol_allowlist"] = [
        s.upper() for s in (risk_raw.get("symbol_allowlist") or [])
    ]
    risk = RiskLimits(**risk_raw)

    strategies = []
    for index, entry in enumerate(raw.get("strategies") or []):
        if not isinstance(entry, dict):
            raise ConfigError(f"strategies[{index}] must be a mapping")
        if "name" not in entry:
            raise ConfigError(f"strategies[{index}] is missing 'name'")
        symbols = [s.upper() for s in (entry.get("symbols") or [])]
        if not symbols:
            raise ConfigError(f"strategies[{index}] ({entry['name']}) has no symbols")
        strategies.append(
            StrategyConfig(
                name=entry["name"],
                symbols=symbols,
                params=entry.get("params") or {},
                enabled=bool(entry.get("enabled", True)),
            )
        )

    config = Config(
        mode=raw.get("mode", "paper"),
        risk=risk,
        strategies=strategies,
        order_type=raw.get("order_type", "limit"),
        limit_slippage_pct=float(raw.get("limit_slippage_pct", 0.002)),
        time_in_force=raw.get("time_in_force", "gfd"),
        allow_extended_hours=bool(raw.get("allow_extended_hours", False)),
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 300)),
        state_dir=_resolve_state_dir(raw.get("state_dir"), path.parent),
    )

    # A symbol a strategy trades but the allowlist omits is almost always a
    # typo, and it would fail silently at the risk gate on every cycle.
    missing = [s for s in config.traded_symbols() if s not in risk.symbol_allowlist]
    if missing:
        raise ConfigError(
            f"These strategy symbols are not in risk.symbol_allowlist: {missing}. "
            "Add them there to confirm you intend to trade them."
        )

    return config


def with_mode(config: Config, mode: str) -> Config:
    """Return a copy of the config forced into another mode."""
    return replace(config, mode=mode)
