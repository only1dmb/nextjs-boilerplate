# Robinhood trading agent

An autonomous trading agent for a single Robinhood account. It reads your
positions, runs strategies you configure, passes every proposed trade through a
stack of risk guards, and places orders — in paper, confirm, or live mode.

> **Read this part.** This trades real money in a real brokerage account.
> Robinhood has no official public API; this uses `robin_stocks`, an unofficial
> client, which can break when Robinhood changes something. Automated trading
> can lose money faster than you can react. The defaults here are deliberately
> small and cautious, and `paper` mode is the default for a reason. Run it in
> paper for a while, then confirm mode, and read the journal both times before
> you consider arming live mode.

## Setup

```bash
cd agent
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

cp .env.example .env              # credentials
cp config.example.yaml config.yaml  # strategies and risk limits
```

Fill in `.env`. For unattended runs set `ROBINHOOD_TOTP_SECRET` to the secret
Robinhood shows when you set up an authenticator app (Settings → Security →
Two-Factor → Authentication App → "can't scan"). The agent then generates its
own MFA codes. Otherwise pass a code with `--mfa` each run.

Credentials are read from the environment only, never from `config.yaml` — the
config is meant to be committable, `.env` is gitignored.

## Use

```bash
.venv/bin/python run.py status      # account, positions, agent state
.venv/bin/python run.py plan        # what this cycle would do — trades nothing
.venv/bin/python run.py backtest    # replay your strategies over history
.venv/bin/python run.py run --once  # one cycle
.venv/bin/python run.py run         # loop until stopped

.venv/bin/python run.py halt        # kill switch — blocks all further orders
.venv/bin/python run.py resume
.venv/bin/python run.py panic       # halt AND cancel every working order
.venv/bin/python run.py journal     # the audit log
```

`plan` is the command to live in. It shows every signal, the exact order it
would produce, and which risk guard blocked it — without sending anything.

## The three modes

| mode | orders reach Robinhood | needs |
|---|---|---|
| `paper` | no, fills are simulated | nothing — this is the default |
| `confirm` | yes, one prompt per order | `mode: confirm` |
| `live` | yes, placed autonomously | `mode: live` **and** `RH_AGENT_ARM_LIVE=I-ACCEPT-THE-RISK` |

Live mode needs two independent keys — a config change alone cannot make the
agent trade unattended, and neither can an environment variable alone. Override
the config's mode for a single run with `--mode paper`.

## Risk guards

Every proposed trade runs the full stack in `risk.py`. Guards can only reject;
none of them can enlarge an order or approve something another guard blocked.
A trade needs *every* guard to pass.

| guard | what it stops |
|---|---|
| halt | anything at all, while `state/HALT` exists |
| market hours | trading outside regular hours, and trading when hours are *unknown* |
| allowlist | any symbol not explicitly listed in `risk.symbol_allowlist` |
| order notional | orders above `max_order_notional` or below `min_order_notional` |
| position concentration | a buy that would push one symbol past `max_position_pct` of the book |
| cash buffer | a buy that would spend past `min_cash_buffer` |
| long-only | selling more than you hold — the agent never opens a short |
| daily trade count | more than `max_trades_per_day` orders in a day |
| PDT | a 4th day trade on an account under $25k |
| daily drawdown | anything, once equity is down `max_daily_drawdown_pct` from the day's open |
| duplicate order | a second order for a symbol that already has one working |
| cooldown | re-trading a symbol inside `per_symbol_cooldown_minutes` |

The drawdown breaker doesn't just block one trade — it halts the agent for the
day. It's checked on every cycle whether or not any strategy fires, so a
portfolio bleeding out on positions you already hold still trips it.

There is no wildcard in the allowlist. Every symbol any strategy touches must
be listed there, and the config refuses to load otherwise — a typo'd ticker
fails at startup rather than silently never trading.

## Strategies

Three ship in `strategies/`. Each is a pure function of market data and
holdings: it proposes signals, and cannot place orders, read your credentials,
or know what mode the agent is in.

- **`sma_crossover`** — buy when the fast SMA crosses above the slow, exit when
  it crosses back. Fires on the *crossing*, not the state, so it won't re-buy
  every cycle the trend holds.
- **`rsi_reversion`** — buy oversold, sell the bounce.
- **`target_weights`** — hold each symbol at a target share of the portfolio,
  trading only when drift leaves the band. The least clever one, and the one
  most people actually want.

When two strategies disagree about a symbol in the same cycle, the sell wins.

Add your own by subclassing `BaseStrategy` and registering it in
`strategies/__init__.py`.

## Backtesting

```bash
.venv/bin/python run.py backtest --span year --verbose
```

Fills happen at the *next* bar's close and pay the configured slippage, so
results are pessimistic by design. It reuses the exact `Strategy` and
`MarketView` types the live engine uses — what you test is what runs.

A backtest still overstates what you'd have made. It can tell you a strategy is
broken; it cannot tell you one is good.

## The journal

Every signal, risk decision, and order attempt is appended to
`state/journal.jsonl` as one JSON object per line, flushed and fsynced. It is
never rewritten. The agent reads it back for the day's trade count, per-symbol
cooldowns, and the day's opening equity, so deleting it mid-session resets
those counters — don't.

```bash
.venv/bin/python run.py journal --event order_submitted
.venv/bin/python run.py journal --json | jq 'select(.event == "risk_decision" and .allowed == false)'
```

## Running it on a schedule

`run` loops in the foreground. For unattended operation, a systemd unit or a
cron entry calling `run --once` on a schedule is usually better — the halt file
is checked at the top of every cycle, so `halt` stops a cron-driven agent just
as immediately as an interactive one.

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

The risk stack, sizing, mode gating, and config validation are the parts worth
trusting; they're what most of the tests cover.

## Limits

- Stocks and ETFs only. No options, no crypto.
- Long only. It will never open a short position.
- Whole shares only for limit orders.
- One account.
- `robin_stocks` is unofficial and unaffiliated with Robinhood. Rate limits and
  auth flows change without notice.
