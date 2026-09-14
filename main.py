"""Trading agent. Two entry points, both scheduled by launchd:

  main.py           decision cycle, 3x/day (com.ies.trading-agent.plist): asks the model, places orders
  main.py --retry   10 min after each decision (com.ies.trading-agent-retry.plist): reruns it only if it failed
  main.py --guard   mechanical exits, every 30 min (com.ies.trading-agent-guard.plist): no model call

Decisions locked on the wayfinder map: https://github.com/mr-j90/trading-ai-agent/issues/1
"""

import fcntl
import html
import json
import os
import sys
import time as time_module
import urllib.request
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal

from alpaca.data.historical import NewsClient, StockHistoricalDataClient
from alpaca.data.requests import NewsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from watchlist import SECTOR_OF

load_dotenv()
KEY, SECRET = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
trading = TradingClient(KEY, SECRET, paper=True)  # ponytail: paper hardcoded; real money is a separate effort
data = StockHistoricalDataClient(KEY, SECRET)
news = NewsClient(KEY, SECRET)
llm = OpenAI()

MODEL = "gpt-5.6-terra"
START_EQUITY = 500.0
MAX_POSITION_PCT = 0.20
MAX_SECTOR_PCT = 0.60
DAILY_STOP_PCT = 0.03
MAX_ORDERS = 5
STOP_LOSS_PCT = 0.08  # from entry
TRAILING_STOP_PCT = 0.10  # from the position's peak price
KILL_EQUITY = 400.0
KILL_BENCHMARK_GAP = 0.15
SUMMARY_WINDOW = timedelta(minutes=90)
SYMBOLS = list(SECTOR_OF)
UTC = timezone.utc

ROOT = Path(__file__).parent
JOURNAL = ROOT / "journal.jsonl"
BENCHMARK = ROOT / "benchmark.json"
PEAKS = ROOT / "peaks.json"  # symbol -> highest price seen while held
HALT = ROOT / "HALT"
LOCK = ROOT / ".lock"


class Order(BaseModel):
    symbol: Literal[*SYMBOLS]
    side: Literal["buy", "sell"]
    notional_usd: float
    reason: str


class Decision(BaseModel):
    orders: list[Order]
    market_view: str


INSTRUCTIONS = f"""You manage a small long-only US equities paper account, starting equity ${START_EQUITY:.0f}.
You may only trade the watchlist symbols given. Orders are dollar-sized market orders that fill immediately.
Hard limits enforced by code (orders that break them are dropped and shown to you next cycle):
- max {MAX_POSITION_PCT:.0%} of equity in any one symbol and {MAX_SECTOR_PCT:.0%} in any one sector; buys limited to available cash
- no buys after equity falls {DAILY_STOP_PCT:.0%} below start-of-day; sells always allowed
- at most {MAX_ORDERS} orders per cycle; sell notional cannot exceed the position's market value
Code also runs mechanical exits every 30 minutes without you: sell a position {STOP_LOSS_PCT:.0%} below entry,
sell {TRAILING_STOP_PCT:.0%} below its peak price, trim anything above the position cap. Do not duplicate those;
spend your attention on entries and on selling when a thesis has broken.
Return an empty orders list to hold. Hold unless something changed since your last entry: trading costs nothing
here but churn rarely helps, and chasing an intraday move that already happened is churn.
market_view is your journal entry: 2-4 sentences on what you see and why you acted or held."""


# ---------- journal ----------
def read_journal() -> list[dict]:
    if not JOURNAL.exists():
        return []
    return [json.loads(line) for line in JOURNAL.read_text().splitlines() if line.strip()]


def append_journal(entry: dict) -> None:
    with JOURNAL.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ---------- market data ----------
def market_snapshot(now: datetime) -> tuple[str, dict[str, float]]:
    daily = data.get_stock_bars(
        StockBarsRequest(symbol_or_symbols=SYMBOLS, timeframe=TimeFrame.Day, start=now - timedelta(days=45))
    ).data
    today_start = datetime.combine(now.date(), time(13, 0), tzinfo=UTC)
    intra = data.get_stock_bars(
        StockBarsRequest(symbol_or_symbols=SYMBOLS, timeframe=TimeFrame(30, TimeFrameUnit.Minute), start=today_start)
    ).data
    lines, last_close = [], {}
    for s in SYMBOLS:
        d = [round(b.close, 2) for b in daily.get(s, [])][-20:]
        i = [round(b.close, 2) for b in intra.get(s, [])]
        if d:
            last_close[s] = i[-1] if i else d[-1]
        lines.append(f"{s} [{SECTOR_OF[s]}] daily closes: {d} | today 30m: {i}")
    return "\n".join(lines), last_close


def headlines(now: datetime) -> list[str]:
    ns = news.get_news(NewsRequest(symbols=",".join(SYMBOLS), start=now - timedelta(hours=24), limit=50))
    items = [n for v in (ns.data.values() if isinstance(ns.data, dict) else [ns.data]) for n in v]
    return [f"{n.created_at:%H:%M} {','.join(sym for sym in n.symbols if sym in SECTOR_OF)}: {n.headline}" for n in items]


# ---------- risk ----------
def validate(orders: list[Order], cash: float, equity: float, market_value: dict[str, float], buys_blocked: bool):
    """Pure. Returns (placed, rejected). market_value is copied, not mutated."""
    mv = dict(market_value)
    cap, sector_cap = MAX_POSITION_PCT * equity, MAX_SECTOR_PCT * equity
    sector_mv = lambda sector: sum(v for s, v in mv.items() if SECTOR_OF.get(s) == sector)
    placed, rejected = [], []
    for o in orders:
        why = None
        if len(placed) >= MAX_ORDERS:
            why = f"max {MAX_ORDERS} orders per cycle"
        elif o.notional_usd < 1:
            why = "below $1 minimum"
        elif o.side == "buy":
            if buys_blocked:
                why = "daily loss stop tripped, no new buys today"
            elif o.notional_usd > cash:
                why = f"exceeds available cash ${cash:.2f}"
            elif mv.get(o.symbol, 0) + o.notional_usd > cap:
                why = f"would exceed position cap ${cap:.2f}"
            elif sector_mv(SECTOR_OF[o.symbol]) + o.notional_usd > sector_cap:
                why = f"would exceed {SECTOR_OF[o.symbol]} sector cap ${sector_cap:.2f}"
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


def guard_orders(positions, equity: float, peaks: dict[str, float]) -> tuple[list[Order], dict[str, float]]:
    """Pure. Mechanical exits: stop-loss from entry, trailing stop from peak, trim to cap. Returns (orders, new_peaks)."""
    cap = MAX_POSITION_PCT * equity
    orders, new_peaks = [], {}
    for p in positions:
        price, entry, mv = float(p.current_price), float(p.avg_entry_price), float(p.market_value)
        peak = max(peaks.get(p.symbol, entry), price)
        new_peaks[p.symbol] = peak
        if price <= entry * (1 - STOP_LOSS_PCT):
            orders.append(Order(symbol=p.symbol, side="sell", notional_usd=mv, reason=f"stop-loss: {price / entry - 1:.1%} from entry {entry:.2f}"))
        elif price <= peak * (1 - TRAILING_STOP_PCT):
            orders.append(Order(symbol=p.symbol, side="sell", notional_usd=mv, reason=f"trailing stop: {price / peak - 1:.1%} from peak {peak:.2f}"))
        elif mv - cap >= 1:
            orders.append(Order(symbol=p.symbol, side="sell", notional_usd=round(mv - cap, 2), reason=f"trim to {MAX_POSITION_PCT:.0%} cap (${cap:.0f})"))
    return orders, new_peaks


def submit(o: Order, market_value: dict[str, float]):
    if o.side == "sell" and o.notional_usd >= 0.98 * market_value.get(o.symbol, 0):
        return trading.close_position(o.symbol)  # avoid a fractional crumb
    return trading.submit_order(
        MarketOrderRequest(symbol=o.symbol, notional=round(o.notional_usd, 2), side=OrderSide(o.side), time_in_force=TimeInForce.DAY)
    )


# ---------- reporting ----------
esc = html.escape  # model text goes inside Telegram HTML


def benchmark_return(last_close: dict[str, float]) -> float | None:
    if not BENCHMARK.exists():
        BENCHMARK.write_text(json.dumps(last_close, indent=1))
        return 0.0
    base = json.loads(BENCHMARK.read_text())
    rets = [last_close[s] / base[s] - 1 for s in base if s in last_close]
    return sum(rets) / len(rets) if rets else None


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


def halt(reason: str) -> None:
    HALT.write_text(reason)
    notify(f"⛔ <b>Trading agent halted:</b> {esc(reason)}")


def daily_summary(equity: float, cash: float, positions, today: list[dict], bench: float | None) -> str:
    agent_ret = equity / START_EQUITY - 1
    lines = [f"<b>Trading agent, {today[-1]['ts'][:10]}</b>"]
    sod = next((e["equity"] for e in today if e.get("equity")), None)
    day = f"{equity / sod - 1:+.2%} today" if sod else ""
    lines.append(f"Equity ${equity:.2f} (cash ${cash:.2f}), {day}, {agent_ret:+.2%} since start")
    if bench is not None:
        lines.append(f"Benchmark (equal-weight hold) {bench:+.2%}, gap {(agent_ret - bench) * 100:+.1f} pts")
    placed = [o for e in today for o in e["placed"]]
    rejected = [o for e in today for o in e["rejected"]]
    lines.append(f"\n<b>Trades ({len(placed)} placed, {len(rejected)} rejected)</b>")
    lines += [f"• {o['side']} ${o['notional_usd']:.0f} {o['symbol']}: {esc(o['reason'])}" for o in placed]
    lines += [f"• ✗ {o['side']} ${o['notional_usd']:.0f} {o['symbol']}: {esc(o['why'])}" for o in rejected]
    lines.append("\n<b>Positions</b>")
    for sector in ("tech", "blue_collar"):
        ps = [p for p in positions if SECTOR_OF.get(p.symbol) == sector]
        if ps:
            lines.append(f"<i>{sector}</i>: " + ", ".join(f"{p.symbol} ${float(p.market_value):.0f} ({float(p.unrealized_plpc):+.1%})" for p in ps))
    lines.append("\n<b>Market view</b>")
    lines += [f"• {e['ts'][11:16]}Z {esc(e['market_view'])}" for e in today if e["market_view"]]
    errors = [e["error"] for e in today if e.get("error")]
    if errors:
        lines.append("\n<b>Errors</b>\n" + "\n".join(f"• {esc(err)}" for err in errors))
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
            print(f"{fn.__name__ if hasattr(fn, '__name__') else 'call'} failed ({e.__class__.__name__}), retrying in {wait:.0f}s")
            time_module.sleep(wait)


def preflight():
    clock = retry(trading.get_clock)
    if not clock.is_open:
        return clock, None, None
    return clock, retry(trading.get_account), retry(trading.get_all_positions)


def cycle(dry_run: bool, summary: bool = True) -> None:
    if HALT.exists():
        print("halted:", HALT.read_text())
        return
    try:
        clock, account, positions = preflight()
    except Exception as e:  # journal it so the dashboard and summary show the gap
        append_journal({"kind": "cycle", "ts": datetime.now(UTC).isoformat(), "equity": None, "cash": None, "market_view": "", "placed": [], "rejected": [], "error": f"preflight: {e!r}"})
        raise
    if account is None:
        print("market closed, next open", clock.next_open)
        return
    now = datetime.now(UTC)
    equity, cash = float(account.equity), float(account.buying_power)
    market_value = {p.symbol: float(p.market_value) for p in positions}
    journal = read_journal()
    today = [e for e in journal if e["ts"][:10] == now.date().isoformat()]
    sod_equity = next((e["equity"] for e in today if e["equity"]), equity)
    buys_blocked = equity < (1 - DAILY_STOP_PCT) * sod_equity
    entry = {"kind": "cycle", "ts": now.isoformat(), "equity": equity, "cash": cash, "market_view": "", "placed": [], "rejected": [], "error": None}
    last_close: dict[str, float] = {}
    try:
        market_text, last_close = market_snapshot(now)
        state = {
            "now_utc": now.isoformat(),
            "minutes_to_close": int((clock.next_close - now).total_seconds() // 60),
            "equity": equity,
            "cash": cash,
            "start_of_day_equity": sod_equity,
            "buys_blocked": buys_blocked,
            "positions": [
                {"symbol": p.symbol, "qty": p.qty, "market_value": p.market_value, "avg_entry": p.avg_entry_price, "unrealized_pl": p.unrealized_pl}
                for p in positions
            ],
            "recent_journal": [{k: e[k] for k in ("ts", "market_view", "placed", "rejected")} for e in journal[-5:]],
            "market": market_text,
            "news_24h": headlines(now),
        }
        resp = llm.responses.parse(
            model=MODEL, reasoning={"effort": "low"}, instructions=INSTRUCTIONS, input=json.dumps(state, default=str), text_format=Decision
        )
        decision = resp.output_parsed or Decision(orders=[], market_view="Model refused to answer; holding.")
        entry["market_view"] = decision.market_view
        placed, rejected = validate(decision.orders, cash, equity, market_value, buys_blocked)
        for o in placed:
            if not dry_run:
                submit(o, market_value)
        entry["placed"], entry["rejected"] = [o.model_dump() for o in placed], rejected
    except Exception as e:  # journal it; the summary surfaces it
        entry["error"] = repr(e)
    print(json.dumps(entry, indent=1, default=str))
    if dry_run:
        return
    append_journal(entry)
    if summary and clock.next_close - now <= SUMMARY_WINDOW:
        bench = benchmark_return(last_close) if last_close else None
        notify(daily_summary(equity, cash, positions, today + [entry], bench))
        if equity < KILL_EQUITY:
            halt(f"equity ${equity:.2f} below ${KILL_EQUITY:.0f}")
        elif bench is not None and (equity / START_EQUITY - 1) - bench < -KILL_BENCHMARK_GAP:
            halt(f"{(equity / START_EQUITY - 1 - bench) * 100:.1f} pts behind benchmark")


def guard(dry_run: bool) -> None:
    if HALT.exists():
        return
    clock, account, positions = preflight()
    if account is None:
        return
    now = datetime.now(UTC)
    peaks = json.loads(PEAKS.read_text()) if PEAKS.exists() else {}
    orders, peaks = guard_orders(positions, float(account.equity), peaks)
    if not dry_run:
        PEAKS.write_text(json.dumps(peaks, indent=1))
    if not orders:
        print(f"guard {now:%H:%M}Z: {len(positions)} positions, nothing to do")
        return
    market_value = {p.symbol: float(p.market_value) for p in positions}
    entry = {"kind": "guard", "ts": now.isoformat(), "equity": float(account.equity), "cash": float(account.buying_power),
             "market_view": "guard: " + "; ".join(f"{o.symbol} {o.reason}" for o in orders), "placed": [], "rejected": [], "error": None}
    try:
        for o in orders:
            if not dry_run:
                submit(o, market_value)
        entry["placed"] = [o.model_dump() for o in orders]
    except Exception as e:
        entry["error"] = repr(e)
    print(json.dumps(entry, indent=1, default=str))
    if not dry_run:
        append_journal(entry)


def retry_cycle() -> None:
    """Runs 10 min after each scheduled decision; only acts if that decision left no successful entry."""
    cutoff = datetime.now(UTC) - timedelta(minutes=20)
    ok = any(e.get("kind", "cycle") == "cycle" and not e["error"] and datetime.fromisoformat(e["ts"]) >= cutoff for e in read_journal())
    if ok:
        print("retry: last decision cycle succeeded, nothing to do")
        return
    print("retry: no successful decision cycle in the last 20 min, running one")
    cycle(dry_run=False)


if __name__ == "__main__":
    with LOCK.open("w") as lock:  # a guard and a decision cycle must not overlap
        fcntl.flock(lock, fcntl.LOCK_EX)
        if "--guard" in sys.argv:
            guard(dry_run="--dry-run" in sys.argv)
        elif "--retry" in sys.argv:
            retry_cycle()
        else:
            cycle(dry_run="--dry-run" in sys.argv, summary="--no-summary" not in sys.argv)
