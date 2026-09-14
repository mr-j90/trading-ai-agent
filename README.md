# trading-ai-agent

An LLM-driven paper-trading agent on Alpaca. A model (OpenAI, `gpt-5.6-terra`) decides trades three times a day across a fixed tech + blue-collar watchlist; code enforces the risk limits regardless of what the model says. A Next.js dashboard shows it live. Planning history and every decision live on the [wayfinder map](https://github.com/mr-j90/trading-ai-agent/issues/1).

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
| `make dry-run` | one decision cycle, prints orders, submits nothing |
| `make guard` | one guard pass, submits nothing |
| `make run` | one **real** decision cycle now (paper orders, no summary) |
| `make install` | copy plists to `~/Library/LaunchAgents` and (re)load the jobs, after editing a schedule |
| `make uninstall` | unload the jobs, keep the files |

## How it runs

Three launchd jobs, all calling `main.py` in this directory and logging to `agent.log`:

| job | when (ET) | does |
|---|---|---|
| `com.ies.trading-agent` | 9:45, 12:30, 15:30 | decision cycle: market clock gate, bars + news for the watchlist, model returns dollar-sized market orders, code validates and submits, journal entry; the 15:30 run sends the Telegram summary |
| `com.ies.trading-agent-retry` | 9:55, 12:40, 15:40 | reruns the decision only if no successful one landed in the last 20 min |
| `com.ies.trading-agent-guard` | every 30 min | mechanical exits, no model call |

A file lock (`.lock`) keeps runs from overlapping. Every run exits immediately when the market is closed.

## Rules the code enforces

- Long only. Notional market orders, DAY, watchlist symbols only.
- Max 20% of equity per symbol, 60% per sector, 5 orders per cycle, $1 minimum.
- Daily loss stop: 3% below start-of-day equity blocks new buys until the next day.
- Guards: sell at 8% below entry, sell at 10% below the position's peak, trim anything over the 20% cap.
- Kill switches, checked at the daily summary: 20% drawdown on contributed capital, or 15 points behind the benchmark, write `HALT` and stop everything. Delete `HALT` to resume.

## Scorekeeping

The benchmark is an equal-weight buy-and-hold of the watchlist. `benchmark.json` is a ledger: contributed capital, the benchmark's share units, and the Alpaca deposit ids already applied. Deposits raise contributed capital and buy the basket the same day, so the agent and the benchmark see identical cash flows. The paper run is judged after 8 weeks: a win is beating the benchmark after model costs (~$5/month).

## Files

| file | purpose |
|---|---|
| `main.py` | the agent: decision cycle, guard, retry, summary |
| `watchlist.py` | the 25 tickers with sector tags (source of truth; mirror in `dashboard/app/watchlist.ts`) |
| `test_main.py` | self-checks for validation, guards, and the ledger |
| `com.ies.trading-agent*.plist` | launchd schedules |
| `dashboard/` | Next.js dashboard; `/api/state` reads journal + Alpaca, `/api/run` triggers a run |
| `journal.jsonl` | one line per run: equity, market view, placed, rejected, error |
| `benchmark.json`, `peaks.json`, `HALT` | ledger, guard high-water marks, halt flag (all gitignored) |
| `docs/research/` | research notes behind the decisions |
