# trading-ai-agent

An LLM-driven paper-trading agent on Alpaca. A model (OpenAI) decides trades three times a day across a fixed tech + blue-collar watchlist; code enforces the risk limits regardless of what the model says. Several **strategies** run side by side, each on its own paper account, and a Next.js dashboard compares them. Planning history and every decision live on the [wayfinder map](https://github.com/mr-j90/trading-ai-agent/issues/1).

## Strategies

`strategies.json` defines them; both the agent and the dashboard read it. Each strategy is a paper account (`key_env` prefix for its keys in `.env`), a model, optional prompt `style`, its own risk numbers, and an `ends` date (default 2027-01-01). State lives in `runs/<name>/`.

| name | idea | needs in `.env` |
|---|---|---|
| `main` | baseline: gpt-5.6-terra, default limits | `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` |
| `luna` | same rules on the cheap model | `ALPACA_LUNA_API_KEY`, `ALPACA_LUNA_SECRET_KEY` |
| `momentum` | concentrated relative strength, 25% cap, 3 orders/cycle | `ALPACA_MOMENTUM_*` |
| `cautious` | 15% cap, 50% sector cap, tighter stops, medium reasoning | `ALPACA_CAUTIOUS_*` |
| `crypto` | BTC/ETH/SOL, 40% cap, wider stops, GTC orders, 24/7 | `ALPACA_CRYPTO_*` |

To add one: create a paper account in the Alpaca dashboard, reset it to $500, add its two keys to `.env`, add an entry to `strategies.json`. Strategies without keys show as "not configured" and are skipped. Every scheduled job runs `main.py --all`, so a new strategy starts at the next cycle with no reload.

A strategy may set `asset_class: "crypto"` and its own `watchlist`. Crypto strategies skip the market-clock gate, use GTC orders, pull bars from Alpaca's crypto endpoint, and treat the 15:30 ET run as the day's last. Decision cycles still follow the weekday schedule; guards run every 30 minutes all week, so stops are enforced on weekends too.

## Setup

```sh
brew install uv node                 # if missing
uv sync                              # python deps
cd dashboard && npm install && cd ..
cp .env.example .env                 # then fill it in, never commit it
make test
```

`.env` needs `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (paper), `OPENAI_API_KEY`, and for the daily summary `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`. The dashboard reads the same file through `dashboard/.env.local`, a symlink to `../.env`.

The Mac must be awake during market hours. One-time, needs sudo:

```sh
sudo pmset repeat wakeorpoweron MTWRF 08:25:00   # Central
```

## Make commands

| command | what it does |
|---|---|
| `make up` | everything on: loads the three launchd jobs and starts the dashboard in the background |
| `make down` | everything off: unloads the jobs and stops the dashboard |
| `make status` | job states, run counts, last exit codes, last three journal entries |
| `make logs` | follow `agent.log` |
| `make dashboard` | dashboard in the foreground on port 3210 (`PORT=4000 make dashboard`) |
| `make test` | Python self-checks + dashboard type-check |
| `make dry-run` | one decision cycle for `main`, prints orders, submits nothing (`S=luna` or `S=--all` to pick) |
| `make guard` | one guard pass, submits nothing (`S=` as above) |
| `make run` | one **real** decision cycle now (paper orders, no summary) (`S=` as above) |
| `make install` | copy plists to `~/Library/LaunchAgents` and (re)load the jobs, after editing a schedule |
| `make uninstall` | unload the jobs, keep the files |

## How it runs

Three launchd jobs, all calling `main.py --all` in this directory (every configured strategy in turn) and logging to `agent.log`:

| job | when (ET) | does |
|---|---|---|
| `com.ies.trading-agent` | 9:45, 12:30, 15:30 | decision cycle: market clock gate, bars + news for the watchlist, model returns dollar-sized market orders, code validates and submits, journal entry; the 15:30 run sends the Telegram summary |
| `com.ies.trading-agent-retry` | 9:55, 12:40, 15:40 | reruns the decision only if no successful one landed in the last 20 min |
| `com.ies.trading-agent-guard` | every 30 min | mechanical exits, no model call |

A per-strategy file lock keeps runs on the same account from overlapping. Every run exits immediately when the market is closed. The dashboard's admin table can run, halt, and resume any strategy.

## Rules the code enforces (defaults; each strategy may override)

- Long only. Notional market orders, DAY, watchlist symbols only.
- Max 20% of equity per symbol, 60% per sector, 5 orders per cycle, $1 minimum.
- Daily loss stop: 3% below start-of-day equity blocks new buys until the next day.
- Guards: sell at 8% below entry, sell at 10% below the position's peak, trim anything over the position cap.
- Kill switches, checked at the daily summary: 20% drawdown on contributed capital, or 15 points behind the benchmark, write `runs/<name>/HALT` and stop that strategy. Delete the file, or press resume in the dashboard, to continue.

## Scorekeeping

The benchmark is an equal-weight buy-and-hold of the watchlist. `benchmark.json` is a ledger: contributed capital, the benchmark's share units, and the Alpaca deposit ids already applied. Deposits raise contributed capital and buy the basket the same day, so the agent and the benchmark see identical cash flows. The paper run is judged after 8 weeks: a win is beating the benchmark after model costs (~$5/month).

## Files

| file | purpose |
|---|---|
| `main.py` | the agent: decision cycle, guard, retry, summary, for one or all strategies |
| `strategies.json`, `strategies.py` | strategy definitions (shared with the dashboard) and the dataclass that loads them |
| `watchlist.py` | the 25 tickers with sector tags (source of truth; mirror in `dashboard/app/watchlist.ts`) |
| `test_main.py` | self-checks for validation, guards, the ledger, and strategy overrides |
| `com.ies.trading-agent*.plist` | launchd schedules |
| `dashboard/` | Next.js dashboard; `/api/strategies` compares, `/api/state?strategy=` details, `/api/run` and `/api/halt` control |
| `runs/<name>/journal.jsonl` | one line per run: kind, equity, market view, placed, rejected, error |
| `runs/<name>/benchmark.json`, `peaks.json`, `HALT` | ledger, guard high-water marks, halt flag (`runs/` is gitignored) |
| `docs/research/` | research notes behind the decisions |
