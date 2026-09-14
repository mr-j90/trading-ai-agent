"""Trading agent. Runs one or all strategies from strategies.json, each on its own Alpaca paper account.

  main.py [--strategy NAME | --all]           decision cycle, 3x/day (com.ies.trading-agent.plist)
  main.py --retry [--strategy NAME | --all]   10 min later: rerun only if that decision failed
  main.py --guard [--strategy NAME | --all]   mechanical exits, every 30 min, no model call
  flags: --dry-run (never submit), --no-summary (skip the Telegram summary)

State per strategy lives in runs/<name>/ (journal.jsonl, benchmark.json, peaks.json, HALT, .lock).
Decisions locked on the wayfinder map: https://github.com/mr-j90/trading-ai-agent/issues/1
"""

import fcntl
import html
import json
import os
import sys
import time as time_module
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from alpaca.data.historical import CryptoHistoricalDataClient, NewsClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, NewsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from strategies import STRATEGIES, Strategy

load_dotenv()
# market data is account-agnostic: data clients on the main keys serve every strategy
data = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
crypto_data = CryptoHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
news = NewsClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
llm = OpenAI()
_clients: dict[str, TradingClient] = {}


def trading(S: Strategy) -> TradingClient:
    if S.name not in _clients:
        k, s = S.keys  # type: ignore[misc]  # callers check S.keys first
        _clients[S.name] = TradingClient(k, s, paper=True)  # ponytail: paper hardcoded; real money is a separate effort
    return _clients[S.name]


SUMMARY_WINDOW = timedelta(minutes=90)
SUMMARY_HOUR_ET = 15  # 24/7 strategies: the run at/after this ET hour is the day's last
# $ per 1M tokens (input, output), from docs/research/openai-sdk.md on 2026-09-14. ponytail: re-check when OpenAI moves prices.
PRICES = {"gpt-6-astra": (10, 50), "gpt-5.6-sol": (4, 20), "gpt-5.6-terra": (2, 12), "gpt-5.6-luna": (0.20, 1.20)}


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float | None:
    p = PRICES.get(model)
    return round((tokens_in * p[0] + tokens_out * p[1]) / 1e6, 4) if p else None
UTC = timezone.utc
ET = ZoneInfo("America/New_York")


class Order(BaseModel):
    symbol: str  # validated against the strategy's watchlist in validate()
    side: Literal["buy", "sell"]
    notional_usd: float
    reason: str


class Decision(BaseModel):
    orders: list[Order]
    market_view: str


def instructions(S: Strategy) -> str:
    what = "crypto" if S.crypto else "US equities"
    return f"""You manage a small long-only {what} paper account, starting equity ${S.start_equity:.0f}.
You may only trade the watchlist symbols given, spelled exactly as given. Orders are dollar-sized market orders that fill immediately.
For each symbol you get computed features (percent changes, distance from the 20-day high/low, 20/50-day moving averages,
20-day volatility, today's volume vs its 20-day average) plus the last 10 daily closes and today's 30-minute closes.
Hard limits enforced by code (orders that break them are dropped and shown to you next cycle):
- max {S.max_position_pct:.0%} of equity in any one symbol and {S.max_sector_pct:.0%} in any one sector; buys limited to available cash
- no buys after equity falls {S.daily_stop_pct:.0%} below start-of-day; sells always allowed
- at most {S.max_orders} orders per cycle; sell notional cannot exceed the position's market value
Code also runs mechanical exits every 30 minutes without you: sell a position {S.stop_loss_pct:.0%} below entry,
sell {S.trailing_stop_pct:.0%} below its peak price, trim anything above the position cap. Do not duplicate those;
spend your attention on entries and on selling when a thesis has broken.
Return an empty orders list to hold. Hold unless something changed since your last entry: trading costs nothing
here but churn rarely helps, and chasing an intraday move that already happened is churn.
{S.style}
market_view is your journal entry: 2-4 sentences on what you see and why you acted or held."""


# ---------- journal ----------
def read_journal(S: Strategy) -> list[dict]:
    p = S.dir / "journal.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def append_journal(S: Strategy, entry: dict) -> None:
    with (S.dir / "journal.jsonl").open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ---------- market data ----------
def bars(S: Strategy, timeframe: TimeFrame, start: datetime) -> dict:
    if S.crypto:
        return crypto_data.get_crypto_bars(CryptoBarsRequest(symbol_or_symbols=S.symbols, timeframe=timeframe, start=start)).data
    return data.get_stock_bars(StockBarsRequest(symbol_or_symbols=S.symbols, timeframe=timeframe, start=start)).data


def features(closes: list[float], volumes: list[float], last: float) -> dict:
    """Pure. What a chart reader looks for, as numbers. `closes` oldest->newest daily closes (>= 2), `last` the latest price."""
    c = closes
    pct = lambda a, b: round((a / b - 1) * 100, 2) if b else None
    ma = lambda n: round(sum(c[-n:]) / n, 2) if len(c) >= n else None
    win = c[-20:]
    rets = [c[i] / c[i - 1] - 1 for i in range(max(1, len(c) - 20), len(c))]
    mean = sum(rets) / len(rets) if rets else 0
    vol = round((sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5 * 100, 2) if len(rets) > 1 else None
    v20 = volumes[-21:-1]
    return {
        "last": round(last, 2),
        "chg_1d_pct": pct(last, c[-2]) if len(c) >= 2 else None,
        "chg_5d_pct": pct(last, c[-6]) if len(c) >= 6 else None,
        "chg_20d_pct": pct(last, c[-21]) if len(c) >= 21 else None,
        "from_20d_high_pct": pct(last, max(win)),
        "from_20d_low_pct": pct(last, min(win)),
        "ma20": ma(20),
        "ma50": ma(50),
        "above_ma20": (last > ma(20)) if ma(20) else None,
        "above_ma50": (last > ma(50)) if ma(50) else None,
        "daily_vol_20d_pct": vol,
        "volume_vs_20d_avg": round(volumes[-1] / (sum(v20) / len(v20)), 2) if v20 and sum(v20) else None,
    }


def market_snapshot(S: Strategy, now: datetime) -> tuple[dict, dict[str, float]]:
    """Per-symbol features + short close series. Returns (market dict for the prompt, last price per symbol)."""
    daily = bars(S, TimeFrame.Day, now - timedelta(days=80))  # ~55 trading days so ma50 exists
    # equities: today's session from ~9am ET; crypto never closes, so show the last 12 hours
    today_start = now - timedelta(hours=12) if S.crypto else datetime.combine(now.date(), time(13, 0), tzinfo=UTC)
    intra = bars(S, TimeFrame(30, TimeFrameUnit.Minute), today_start)
    market, last_close = {}, {}
    sector_of = S.sector_of
    for s in S.symbols:
        d = daily.get(s, [])
        i = [round(b.close, 2) for b in intra.get(s, [])]
        if not d:
            continue
        last = i[-1] if i else d[-1].close
        last_close[s] = last
        # today's partial bar would double-count in the 20-day window; use completed days only
        completed = d[:-1] if d[-1].timestamp.date() == now.date() else d
        closes, vols = [b.close for b in completed], [b.volume for b in completed] + [d[-1].volume]
        market[s] = {"sector": sector_of[s], **features(closes, vols, last), "recent_closes": [round(x, 2) for x in closes[-10:]],
                     ("last_12h_30m" if S.crypto else "today_30m"): i}
    return market, last_close


def headlines(S: Strategy, now: datetime) -> list[str]:
    ns = news.get_news(NewsRequest(symbols=",".join(S.symbols), start=now - timedelta(hours=24), limit=50))
    items = [n for v in (ns.data.values() if isinstance(ns.data, dict) else [ns.data]) for n in v]
    known = {s.replace("/", "") for s in S.symbols}
    return [f"{n.created_at:%H:%M} {','.join(sym for sym in n.symbols if sym.replace('/', '') in known)}: {n.headline}" for n in items]


# ---------- risk ----------
def validate(S: Strategy, orders: list[Order], cash: float, equity: float, market_value: dict[str, float], buys_blocked: bool):
    """Pure. Returns (placed, rejected). market_value is copied, not mutated."""
    mv = dict(market_value)
    cap, sector_cap = S.max_position_pct * equity, S.max_sector_pct * equity
    sector_of = S.sector_of
    sector_mv = lambda sector: sum(v for s, v in mv.items() if sector_of.get(s) == sector)
    placed, rejected = [], []
    for o in orders:
        why = None
        if o.symbol not in sector_of:
            why = "not on this strategy's watchlist"
        elif len(placed) >= S.max_orders:
            why = f"max {S.max_orders} orders per cycle"
        elif o.notional_usd < 1:
            why = "below $1 minimum"
        elif o.side == "buy":
            if buys_blocked:
                why = "daily loss stop tripped, no new buys today"
            elif o.notional_usd > cash:
                why = f"exceeds available cash ${cash:.2f}"
            elif mv.get(o.symbol, 0) + o.notional_usd > cap:
                why = f"would exceed position cap ${cap:.2f}"
            elif sector_mv(sector_of[o.symbol]) + o.notional_usd > sector_cap:
                why = f"would exceed {sector_of[o.symbol]} sector cap ${sector_cap:.2f}"
        elif o.notional_usd > mv.get(o.symbol, 0):
            why = f"exceeds held market value ${mv.get(o.symbol, 0):.2f}"
        if why:
            rejected.append({**o.model_dump(), "why": why})
            continue
        placed.append(o)
        if o.side == "buy":
            cash -= o.notional_usd
            mv[o.symbol] = mv.get(o.symbol, 0) + o.notional_usd
        else:  # sale proceeds are not spent in the same cycle
            mv[o.symbol] -= o.notional_usd
    return placed, rejected


def guard_orders(S: Strategy, positions, equity: float, peaks: dict[str, float]) -> tuple[list[Order], dict[str, float]]:
    """Pure. Mechanical exits: stop-loss from entry, trailing stop from peak, trim to cap. Returns (orders, new_peaks)."""
    cap = S.max_position_pct * equity
    orders, new_peaks = [], {}
    for p in positions:
        sym = S.canon(p.symbol)
        price, entry, mv = float(p.current_price), float(p.avg_entry_price), float(p.market_value)
        peak = max(peaks.get(sym, entry), price)
        new_peaks[sym] = peak
        if price <= entry * (1 - S.stop_loss_pct):
            orders.append(Order(symbol=sym, side="sell", notional_usd=mv, reason=f"stop-loss: {price / entry - 1:.1%} from entry {entry:.2f}"))
        elif price <= peak * (1 - S.trailing_stop_pct):
            orders.append(Order(symbol=sym, side="sell", notional_usd=mv, reason=f"trailing stop: {price / peak - 1:.1%} from peak {peak:.2f}"))
        elif mv - cap >= 1:
            orders.append(Order(symbol=sym, side="sell", notional_usd=round(mv - cap, 2), reason=f"trim to {S.max_position_pct:.0%} cap (${cap:.0f})"))
    return orders, new_peaks


def submit(S: Strategy, o: Order, market_value: dict[str, float]):
    if o.side == "sell" and o.notional_usd >= 0.98 * market_value.get(o.symbol, 0):
        return trading(S).close_position(o.symbol.replace("/", ""))  # avoid a fractional crumb; positions API wants BTCUSD
    tif = TimeInForce.GTC if S.crypto else TimeInForce.DAY  # Alpaca crypto accepts only gtc/ioc
    return trading(S).submit_order(
        MarketOrderRequest(symbol=o.symbol, notional=round(o.notional_usd, 2), side=OrderSide(o.side), time_in_force=tif)
    )


# ---------- reporting ----------
esc = html.escape  # model text goes inside Telegram HTML


def apply_deposit(ledger: dict, amount: float, prices: dict[str, float]) -> dict:
    """Pure. Add a cash flow to contributed capital; a deposit also buys the equal-weight basket at `prices`.
    Withdrawals only reduce contributed capital (ponytail: benchmark keeps its shares; sell pro-rata if withdrawals become routine)."""
    out = {**ledger, "units": dict(ledger.get("units", {})), "contributed": ledger.get("contributed", 0.0) + amount}
    if amount > 0 and prices:
        per = amount / len(prices)
        for s, p in prices.items():
            out["units"][s] = out["units"].get(s, 0.0) + per / p
    return out


def benchmark_value(ledger: dict, prices: dict[str, float]) -> float:
    return sum(u * prices[s] for s, u in ledger["units"].items() if s in prices)


def sync_ledger(S: Strategy, prices: dict[str, float]) -> dict:
    """Load the benchmark/contributions ledger, creating it on day one and folding in any new Alpaca cash deposits."""
    p = S.dir / "benchmark.json"
    if p.exists():
        ledger = json.loads(p.read_text())
    else:
        ledger = apply_deposit({"contributed": 0.0, "units": {}, "seen": []}, S.start_equity, prices)
    for a in trading(S).get("/account/activities", {"activity_types": "CSD,CSW"}):  # empty on paper; real deposits show up here
        if a["id"] not in ledger["seen"] and a.get("status") != "canceled":
            ledger = apply_deposit(ledger, float(a["net_amount"]), prices)
            ledger["seen"].append(a["id"])
    p.write_text(json.dumps(ledger, indent=1))
    return ledger


def notify(text: str) -> None:
    """Telegram sendMessage (HTML). Falls back to stdout when the bot isn't configured."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("\n[telegram not configured, summary follows]\n" + text)
        return
    for i in range(0, len(text), 4000):  # Telegram caps a message at 4096 chars
        body = json.dumps({"chat_id": chat, "text": text[i : i + 4000], "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", body, {"Content-Type": "application/json"})
        for attempt in range(3):  # seen: transient TLS resets from api.telegram.org
            try:
                urllib.request.urlopen(req, timeout=10)
                break
            except OSError as e:
                if attempt == 2:
                    print("telegram send failed:", e, "\n" + text)
                    return
                time_module.sleep(2)


def halt(S: Strategy, reason: str) -> None:
    (S.dir / "HALT").write_text(reason)
    notify(f"⛔ <b>[{S.name}] halted:</b> {esc(reason)}")


def daily_summary(S: Strategy, equity: float, cash: float, positions, today: list[dict], contributed: float, bench_value: float | None) -> str:
    agent_ret = equity / contributed - 1
    lines = [f"<b>[{S.name}] {today[-1]['ts'][:10]}</b> · {S.model}"]
    sod = next((e["equity"] for e in today if e.get("equity")), None)
    day = f"{equity / sod - 1:+.2%} today" if sod else ""
    lines.append(f"Equity ${equity:.2f} (cash ${cash:.2f}), {day}, {agent_ret:+.2%} on ${contributed:.0f} contributed")
    if bench_value is not None:
        bench = bench_value / contributed - 1
        lines.append(f"Benchmark (equal-weight hold) ${bench_value:.2f}, {bench:+.2%}, gap {(agent_ret - bench) * 100:+.1f} pts")
    placed = [o for e in today for o in e["placed"]]
    rejected = [o for e in today for o in e["rejected"]]
    lines.append(f"\n<b>Trades ({len(placed)} placed, {len(rejected)} rejected)</b>")
    lines += [f"• {o['side']} ${o['notional_usd']:.0f} {o['symbol']}: {esc(o['reason'])}" for o in placed]
    lines += [f"• ✗ {o['side']} ${o['notional_usd']:.0f} {o['symbol']}: {esc(o['why'])}" for o in rejected]
    lines.append("\n<b>Positions</b>")
    sector_of = S.sector_of
    for sector in S.watchlist:
        ps = [p for p in positions if sector_of.get(S.canon(p.symbol)) == sector]
        if ps:
            lines.append(f"<i>{sector}</i>: " + ", ".join(f"{S.canon(p.symbol)} ${float(p.market_value):.0f} ({float(p.unrealized_plpc):+.1%})" for p in ps))
    lines.append("\n<b>Market view</b>")
    lines += [f"• {e['ts'][11:16]}Z {esc(e['market_view'])}" for e in today if e["market_view"]]
    errors = [e["error"] for e in today if e.get("error")]
    if errors:
        lines.append("\n<b>Errors</b>\n" + "\n".join(f"• {esc(err)}" for err in errors))
    spent = sum(e.get("cost_usd") or 0 for e in today)
    if spent:
        lines.append(f"\nModel spend today ${spent:.3f}")
    return "\n".join(lines)


# ---------- cycle ----------
def retry(fn, attempts: int = 4, wait: float = 15.0):
    """Ride out brief network outages (seen: DNS failure that killed the 15:30 run on day 1)."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1:
                raise
            print(f"{getattr(fn, '__name__', 'call')} failed ({e.__class__.__name__}), retrying in {wait:.0f}s")
            time_module.sleep(wait)


def preflight(S: Strategy):
    t = trading(S)
    clock = retry(t.get_clock)
    if not clock.is_open and not S.crypto:  # crypto never closes
        return clock, None, None
    return clock, retry(t.get_account), retry(t.get_all_positions)


def is_last_run_of_day(S: Strategy, clock, now: datetime) -> bool:
    if S.crypto:
        return now.astimezone(ET).hour >= SUMMARY_HOUR_ET
    return clock.next_close - now <= SUMMARY_WINDOW


def skip_reason(S: Strategy) -> str | None:
    if not S.keys:
        return f"no {S.key_env}_API_KEY / _SECRET_KEY in .env"
    if (S.dir / "HALT").exists():
        return "halted: " + (S.dir / "HALT").read_text()
    if date.today().isoformat() >= S.ends:
        return f"ended {S.ends}"
    return None


def cycle(S: Strategy, dry_run: bool, summary: bool = True) -> None:
    try:
        clock, account, positions = preflight(S)
    except Exception as e:  # journal it so the dashboard and summary show the gap
        append_journal(S, {"kind": "cycle", "ts": datetime.now(UTC).isoformat(), "equity": None, "cash": None, "market_view": "", "placed": [], "rejected": [], "error": f"preflight: {e!r}"})
        raise
    if account is None:
        print("market closed, next open", clock.next_open)
        return
    now = datetime.now(UTC)
    equity, cash = float(account.equity), float(account.buying_power)
    market_value = {S.canon(p.symbol): float(p.market_value) for p in positions}
    journal = read_journal(S)
    today = [e for e in journal if e["ts"][:10] == now.date().isoformat()]
    sod_equity = next((e["equity"] for e in today if e["equity"]), equity)
    buys_blocked = equity < (1 - S.daily_stop_pct) * sod_equity
    entry = {"kind": "cycle", "ts": now.isoformat(), "equity": equity, "cash": cash, "market_view": "", "placed": [], "rejected": [], "error": None}
    last_close: dict[str, float] = {}
    try:
        market, last_close = market_snapshot(S, now)
        state = {
            "now_utc": now.isoformat(),
            "market_hours": "24/7" if S.crypto else f"{int((clock.next_close - now).total_seconds() // 60)} minutes to close",
            "watchlist": S.watchlist,
            "equity": equity,
            "cash": cash,
            "start_of_day_equity": sod_equity,
            "buys_blocked": buys_blocked,
            "positions": [
                {"symbol": S.canon(p.symbol), "qty": p.qty, "market_value": p.market_value, "avg_entry": p.avg_entry_price, "unrealized_pl": p.unrealized_pl}
                for p in positions
            ],
            "recent_journal": [{k: e[k] for k in ("ts", "market_view", "placed", "rejected")} for e in journal[-5:]],
            "market": market,
            "news_24h": headlines(S, now),
        }
        resp = llm.responses.parse(
            model=S.model, reasoning={"effort": S.reasoning}, instructions=instructions(S), input=json.dumps(state, default=str), text_format=Decision
        )
        decision = resp.output_parsed or Decision(orders=[], market_view="Model refused to answer; holding.")
        u = resp.usage
        entry["tokens_in"], entry["tokens_out"] = (u.input_tokens, u.output_tokens) if u else (None, None)
        entry["cost_usd"] = cost_usd(S.model, u.input_tokens, u.output_tokens) if u else None
        entry["market_view"] = decision.market_view
        placed, rejected = validate(S, decision.orders, cash, equity, market_value, buys_blocked)
        for o in placed:
            if not dry_run:
                submit(S, o, market_value)
        entry["placed"], entry["rejected"] = [o.model_dump() for o in placed], rejected
        if not dry_run:  # what it saw and what it said, for the dashboard's "what it saw" view
            (S.dir / "prompts").mkdir(exist_ok=True)
            (S.dir / "prompts" / f"{now:%Y%m%dT%H%M%S}Z.json").write_text(json.dumps(
                {"ts": entry["ts"], "model": S.model, "reasoning": S.reasoning, "instructions": instructions(S), "input": state,
                 "output": decision.model_dump(), "usage": {"input_tokens": entry["tokens_in"], "output_tokens": entry["tokens_out"], "cost_usd": entry["cost_usd"]}},
                indent=1, default=str))
    except Exception as e:  # journal it; the summary surfaces it
        entry["error"] = repr(e)
    print(json.dumps(entry, indent=1, default=str))
    if dry_run:
        return
    append_journal(S, entry)
    if summary and is_last_run_of_day(S, clock, now):
        ledger = sync_ledger(S, last_close) if last_close else None
        contributed = ledger["contributed"] if ledger else S.start_equity
        bench_value = benchmark_value(ledger, last_close) if ledger else None
        notify(daily_summary(S, equity, cash, positions, today + [entry], contributed, bench_value))
        agent_ret = equity / contributed - 1
        if agent_ret < -S.kill_drawdown_pct:
            halt(S, f"equity ${equity:.2f} is {agent_ret:.1%} on ${contributed:.0f} contributed")
        elif bench_value is not None and agent_ret - (bench_value / contributed - 1) < -S.kill_benchmark_gap:
            halt(S, f"{(agent_ret - (bench_value / contributed - 1)) * 100:.1f} pts behind benchmark")


def guard(S: Strategy, dry_run: bool) -> None:
    clock, account, positions = preflight(S)
    if account is None:
        return
    now = datetime.now(UTC)
    peaks_file = S.dir / "peaks.json"
    peaks = json.loads(peaks_file.read_text()) if peaks_file.exists() else {}
    orders, peaks = guard_orders(S, positions, float(account.equity), peaks)
    if not dry_run:
        peaks_file.write_text(json.dumps(peaks, indent=1))
    if not orders:
        print(f"guard {now:%H:%M}Z: {len(positions)} positions, nothing to do")
        return
    market_value = {S.canon(p.symbol): float(p.market_value) for p in positions}
    entry = {"kind": "guard", "ts": now.isoformat(), "equity": float(account.equity), "cash": float(account.buying_power),
             "market_view": "guard: " + "; ".join(f"{o.symbol} {o.reason}" for o in orders), "placed": [], "rejected": [], "error": None}
    try:
        for o in orders:
            if not dry_run:
                submit(S, o, market_value)
        entry["placed"] = [o.model_dump() for o in orders]
    except Exception as e:
        entry["error"] = repr(e)
    print(json.dumps(entry, indent=1, default=str))
    if not dry_run:
        append_journal(S, entry)


def retry_cycle(S: Strategy) -> None:
    """Runs 10 min after each scheduled decision; only acts if that decision left no successful entry."""
    cutoff = datetime.now(UTC) - timedelta(minutes=20)
    ok = any(e.get("kind", "cycle") == "cycle" and not e["error"] and datetime.fromisoformat(e["ts"]) >= cutoff for e in read_journal(S))
    if ok:
        print("retry: last decision cycle succeeded, nothing to do")
        return
    print("retry: no successful decision cycle in the last 20 min, running one")
    cycle(S, dry_run=False)


def run(S: Strategy, argv: list[str]) -> None:
    print(f"== {S.name} ({S.model})")
    if reason := skip_reason(S):
        print("skip:", reason)
        return
    with (S.dir / ".lock").open("w") as lock:  # a guard and a decision cycle on the same account must not overlap
        fcntl.flock(lock, fcntl.LOCK_EX)
        if "--guard" in argv:
            guard(S, dry_run="--dry-run" in argv)
        elif "--retry" in argv:
            retry_cycle(S)
        else:
            cycle(S, dry_run="--dry-run" in argv, summary="--no-summary" not in argv)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--all" in argv:
        chosen = list(STRATEGIES.values())
    else:
        name = argv[argv.index("--strategy") + 1] if "--strategy" in argv else "main"
        chosen = [STRATEGIES[name]]
    failures = 0
    for S in chosen:
        try:
            run(S, argv)
        except Exception as e:  # one strategy's failure must not stop the others
            failures += 1
            print(f"!! {S.name} failed: {e!r}")
    sys.exit(1 if failures else 0)
