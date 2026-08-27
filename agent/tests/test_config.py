"""Config validation is the first safety gate -- a bad limit must not load."""

import textwrap

import pytest

from robinhood_agent.config import (
    LIVE_ARM_ENV,
    LIVE_ARM_VALUE,
    ConfigError,
    load_config,
)


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return path


BASE = """
    mode: paper
    risk:
      symbol_allowlist: [AAPL]
    strategies:
      - name: sma_crossover
        symbols: [AAPL]
"""


def test_loads_a_valid_config(tmp_path):
    config = load_config(write(tmp_path, BASE))
    assert config.mode == "paper"
    assert config.traded_symbols() == ["AAPL"]
    assert config.state_dir == tmp_path / "state"


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="No config at"):
        load_config(tmp_path / "nope.yaml")


def test_unknown_top_level_key(tmp_path):
    with pytest.raises(ConfigError, match="Unknown config keys"):
        load_config(write(tmp_path, BASE + "    yolo: true\n"))


def test_unknown_risk_key_is_not_silently_ignored(tmp_path):
    """A typo'd limit would otherwise fall back to a permissive default."""
    body = """
        mode: paper
        risk:
          symbol_allowlist: [AAPL]
          max_order_notionall: 5
        strategies:
          - name: sma_crossover
            symbols: [AAPL]
    """
    with pytest.raises(ConfigError, match="Unknown risk keys"):
        load_config(write(tmp_path, body))


def test_bad_mode(tmp_path):
    with pytest.raises(ConfigError, match="mode must be one of"):
        load_config(write(tmp_path, BASE.replace("mode: paper", "mode: yolo")))


def test_strategy_symbol_must_be_allowlisted(tmp_path):
    body = """
        mode: paper
        risk:
          symbol_allowlist: [AAPL]
        strategies:
          - name: sma_crossover
            symbols: [AAPL, TSLA]
    """
    with pytest.raises(ConfigError, match="not in risk.symbol_allowlist"):
        load_config(write(tmp_path, body))


def test_symbols_are_uppercased(tmp_path):
    body = """
        mode: paper
        risk:
          symbol_allowlist: [aapl]
        strategies:
          - name: sma_crossover
            symbols: [aapl]
    """
    assert load_config(write(tmp_path, body)).traded_symbols() == ["AAPL"]


def test_strategy_needs_symbols(tmp_path):
    body = """
        mode: paper
        risk:
          symbol_allowlist: [AAPL]
        strategies:
          - name: sma_crossover
            symbols: []
    """
    with pytest.raises(ConfigError, match="has no symbols"):
        load_config(write(tmp_path, body))


def test_disabled_strategy_symbols_are_not_traded(tmp_path):
    body = """
        mode: paper
        risk:
          symbol_allowlist: [AAPL]
        strategies:
          - name: sma_crossover
            enabled: false
            symbols: [AAPL]
    """
    assert load_config(write(tmp_path, body)).traded_symbols() == []


def test_poll_interval_floor(tmp_path):
    with pytest.raises(ConfigError, match="poll_interval_seconds"):
        load_config(write(tmp_path, BASE + "    poll_interval_seconds: 5\n"))


@pytest.mark.parametrize(
    "line,match",
    [
        ("max_position_pct: 1.5", "max_position_pct"),
        ("max_order_notional: 0.5", "max_order_notional"),
        ("min_order_notional: 0", "min_order_notional"),
        ("max_daily_drawdown_pct: 0", "max_daily_drawdown_pct"),
    ],
)
def test_risk_bounds(tmp_path, line, match):
    body = f"""
        mode: paper
        risk:
          symbol_allowlist: [AAPL]
          {line}
        strategies:
          - name: sma_crossover
            symbols: [AAPL]
    """
    with pytest.raises(ConfigError, match=match):
        load_config(write(tmp_path, body))


# -- the live arm ----------------------------------------------------------


def test_paper_and_confirm_need_no_arm(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_ARM_ENV, raising=False)
    for mode in ("paper", "confirm"):
        config = load_config(write(tmp_path, BASE.replace("mode: paper", f"mode: {mode}")))
        config.check_live_arm()  # must not raise


def test_live_without_env_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_ARM_ENV, raising=False)
    config = load_config(write(tmp_path, BASE.replace("mode: paper", "mode: live")))
    with pytest.raises(ConfigError, match=LIVE_ARM_ENV):
        config.check_live_arm()


def test_live_with_wrong_env_value_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_ARM_ENV, "sure")
    config = load_config(write(tmp_path, BASE.replace("mode: paper", "mode: live")))
    with pytest.raises(ConfigError):
        config.check_live_arm()


def test_live_with_both_keys_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_ARM_ENV, LIVE_ARM_VALUE)
    config = load_config(write(tmp_path, BASE.replace("mode: paper", "mode: live")))
    config.check_live_arm()
    assert config.places_real_orders
