"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .backtest import run_backtest
from .client import BrokerError, RobinhoodClient
from .config import Config, ConfigError, load_config, with_mode
from .engine import Engine
from .journal import Journal

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader so credentials stay out of the shell history."""
    if not path.exists():
        return
    import os

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _setup(args: argparse.Namespace, *, login: bool = True) -> tuple[Config, Engine]:
    config = load_config(args.config)
    if getattr(args, "mode", None):
        config = with_mode(config, args.mode)
    config.state_dir.mkdir(parents=True, exist_ok=True)

    # Fail on a missing live arm before touching the network, so a
    # misconfigured live run reports the real problem instead of a login error.
    config.check_live_arm()

    journal = Journal(config.journal_file)
    client = RobinhoodClient(session_path=config.session_file)
    if login:
        client.login(mfa_code=getattr(args, "mfa", None))
    return config, Engine(client, config, journal)


def _banner(config: Config) -> None:
    label = {
        "paper": "PAPER -- simulated, no orders reach Robinhood",
        "confirm": "CONFIRM -- real orders, each one prompts first",
        "live": "LIVE -- real orders, placed autonomously",
    }[config.mode]
    print(f"mode: {label}")


# -- commands --------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    config, engine = _setup(args)
    _banner(config)

    snapshot = engine.client.snapshot()
    print(f"\nequity   ${snapshot.equity:>12,.2f}")
    print(f"cash     ${snapshot.cash:>12,.2f}")
    print(f"buying   ${snapshot.buying_power:>12,.2f}")

    if snapshot.positions:
        print(f"\n{'symbol':<8}{'qty':>10}{'price':>12}{'value':>14}{'P/L':>14}")
        for symbol in sorted(snapshot.positions):
            p = snapshot.positions[symbol]
            print(
                f"{symbol:<8}{p.quantity:>10.4f}{p.price:>12,.2f}"
                f"{p.market_value:>14,.2f}{p.unrealized_pl:>+14,.2f}"
            )
    else:
        print("\nno open stock positions")

    today = date.today()
    print(f"\ntrades today: {len(engine.journal.filled_orders_on(today))}"
          f" / {config.risk.max_trades_per_day}")
    market = engine.client.market_is_open()
    print(f"market open:  {'yes' if market else 'no' if market is False else 'unknown'}")
    if engine.is_halted():
        print(f"\nHALTED: {config.halt_file.read_text().strip()}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    config, engine = _setup(args)
    _banner(config)
    print("\ndry run -- nothing will be sent to Robinhood\n")

    report = engine.run_cycle(execute=False)
    if report.error:
        print(f"error: {report.error}")
        return 1
    if not report.signals:
        print("no signals this cycle")
        return 0

    for result in report.results:
        intent = result.intent
        verdict = "WOULD PLACE" if result.detail == "would place" else "BLOCKED"
        print(
            f"{verdict:<12}{intent.side.upper():<5}{intent.quantity:>8g} "
            f"{intent.symbol:<6} @ ${intent.price:,.2f} = ${intent.notional:,.2f}"
        )
        print(f"            why: {intent.reason}")
        if verdict == "BLOCKED":
            print(f"            blocked by: {result.detail}")
    for note in report.skipped:
        print(f"SKIPPED     {note}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config, engine = _setup(args)
    _banner(config)

    if engine.is_halted():
        print(f"\nagent is halted: {config.halt_file.read_text().strip()}")
        print("run `resume` to clear it")
        return 1

    if args.once:
        report = engine.run_cycle(execute=True)
        if report.error:
            print(f"error: {report.error}")
            return 1
        for result in report.results:
            mark = "placed" if result.placed else "skipped"
            print(
                f"{mark:<8}{result.intent.side.upper():<5}"
                f"{result.intent.quantity:>8g} {result.intent.symbol:<6} "
                f"-- {result.detail}"
            )
        if not report.results:
            print("no actionable signals")
        return 0

    print(f"\nlooping every {config.poll_interval_seconds}s. Ctrl-C to stop.\n")
    try:
        engine.run_forever(max_cycles=args.cycles)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_halt(args: argparse.Namespace) -> int:
    config, engine = _setup(args, login=False)
    engine.halt(args.reason or "halted from the command line")
    print(f"halted. {config.halt_file} written.")
    print("no further orders will be placed until you run `resume`.")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    _, engine = _setup(args, login=False)
    if engine.resume():
        print("resumed")
    else:
        print("agent was not halted")
    return 0


def cmd_panic(args: argparse.Namespace) -> int:
    """Halt and cancel every working order."""
    config, engine = _setup(args)
    engine.halt("panic: halt + cancel all open orders")
    try:
        engine.client.cancel_all_open_orders()
        print("halted and cancelled all open stock orders")
    except BrokerError as exc:
        print(f"halted, but cancelling orders failed: {exc}", file=sys.stderr)
        return 1
    engine.journal.write("panic")
    return 0


def cmd_journal(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    journal = Journal(config.journal_file)
    records = list(journal.read())
    if args.event:
        records = [r for r in records if r.get("event") == args.event]
    for record in records[-args.limit :]:
        if args.json:
            print(json.dumps(record))
        else:
            ts = str(record.get("ts", ""))[:19].replace("T", " ")
            rest = {k: v for k, v in record.items() if k not in ("ts", "event")}
            print(f"{ts}  {record.get('event',''):<18}{rest}")
    if not records:
        print("journal is empty")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    config, engine = _setup(args)

    specs = [s for s in config.strategies if s.enabled]
    if args.strategy:
        specs = [s for s in specs if s.name == args.strategy]
    if not specs:
        print("no matching enabled strategies", file=sys.stderr)
        return 1

    print(f"span={args.span}  start equity=${args.equity:,.0f}  "
          f"slippage={config.limit_slippage_pct:.2%}\n")
    print(f"{'strategy':<18}{'sym':<6}{'trades':>7}{'return':>10}{'B&H':>10}{'maxDD':>9}")

    for spec in specs:
        for symbol in spec.symbols:
            try:
                closes = engine.client.historical_closes(symbol, interval="day", span=args.span)
            except Exception as exc:
                print(f"{spec.name:<18}{symbol:<6}  history failed: {exc}")
                continue
            if len(closes) < args.warmup + 5:
                print(f"{spec.name:<18}{symbol:<6}  not enough history "
                      f"({len(closes)} bars)")
                continue

            result = run_backtest(
                symbol, closes, spec.name, spec.params, config,
                start_equity=args.equity, warmup=args.warmup,
            )
            print(
                f"{spec.name:<18}{symbol:<6}{result.trades:>7}"
                f"{result.total_return:>9.1%}{result.buy_and_hold_return:>10.1%}"
                f"{result.max_drawdown:>9.1%}"
            )
            if args.verbose:
                for fill in result.fills:
                    print(f"    bar {fill.bar:>4} {fill.side:<5}{fill.quantity:>6g} "
                          f"@ {fill.price:>9,.2f}  {fill.reason}")

    print("\nPast results do not predict future results. Backtests overstate "
          "returns even with slippage modelled.")
    return 0


# -- wiring ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="robinhood-agent",
        description="An autonomous trading agent for a Robinhood account.",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="path to config.yaml")
    parser.add_argument("--mode", choices=("paper", "confirm", "live"),
                        help="override the config's mode for this run")
    parser.add_argument("--mfa", help="MFA code, if not using ROBINHOOD_TOTP_SECRET")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="account, positions, and agent state").set_defaults(
        func=cmd_status
    )

    sub.add_parser(
        "plan", help="show what this cycle would do, without trading"
    ).set_defaults(func=cmd_plan)

    run = sub.add_parser("run", help="run the agent")
    run.add_argument("--once", action="store_true", help="one cycle, then exit")
    run.add_argument("--cycles", type=int, help="stop after N cycles")
    run.set_defaults(func=cmd_run)

    halt = sub.add_parser("halt", help="kill switch: block all further orders")
    halt.add_argument("--reason", help="note stored with the halt")
    halt.set_defaults(func=cmd_halt)

    sub.add_parser("resume", help="clear the halt").set_defaults(func=cmd_resume)

    sub.add_parser(
        "panic", help="halt and cancel every working order"
    ).set_defaults(func=cmd_panic)

    journal = sub.add_parser("journal", help="read the audit log")
    journal.add_argument("--limit", type=int, default=40)
    journal.add_argument("--event", help="filter to one event type")
    journal.add_argument("--json", action="store_true")
    journal.set_defaults(func=cmd_journal)

    backtest = sub.add_parser("backtest", help="replay strategies over history")
    backtest.add_argument("--strategy", help="only this strategy")
    backtest.add_argument("--span", default="year",
                          choices=("month", "3month", "year", "5year"))
    backtest.add_argument("--equity", type=float, default=10_000.0)
    backtest.add_argument("--warmup", type=int, default=60)
    backtest.add_argument("--verbose", action="store_true", help="list every fill")
    backtest.set_defaults(func=cmd_backtest)

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, BrokerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
