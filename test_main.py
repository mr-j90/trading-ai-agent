"""Self-check for the money path. Run: uv run python test_main.py"""

from types import SimpleNamespace as P

from main import Order, apply_deposit, benchmark_value, guard_orders, validate
from strategies import STRATEGIES, Strategy

S = STRATEGIES["main"]
equity, cash = 500.0, 300.0
held = {"IESC": 150.0, "NVDA": 50.0}


def o(side, sym, usd):
    return Order(symbol=sym, side=side, notional_usd=usd, reason="t")


# cap: 20% of 500 = 100. NVDA holds 50, so +60 breaks it, +50 fits exactly.
placed, rejected = validate(S, [o("buy", "NVDA", 60), o("buy", "NVDA", 50)], cash, equity, held, False)
assert [r["symbol"] for r in rejected] == ["NVDA"] and "cap" in rejected[0]["why"]
assert len(placed) == 1 and placed[0].notional_usd == 50

# cash runs out across a cycle (mixed sectors so the sector cap stays out of the way)
placed, rejected = validate(S, [o("buy", "AAPL", 100), o("buy", "PWR", 100), o("buy", "META", 100), o("buy", "CAT", 100)], cash, equity, held, False)
assert len(placed) == 3 and "cash" in rejected[0]["why"]

# sells: cannot exceed holding; proceeds don't fund same-cycle buys
placed, rejected = validate(S, [o("sell", "IESC", 200), o("sell", "IESC", 150), o("buy", "CAT", 320)], cash, equity, held, False)
assert [r["symbol"] for r in rejected] == ["IESC", "CAT"]
assert placed[0].notional_usd == 150

# loss stop blocks buys, not sells
placed, rejected = validate(S, [o("buy", "AAPL", 10), o("sell", "NVDA", 10)], cash, equity, held, True)
assert len(placed) == 1 and placed[0].side == "sell" and "loss stop" in rejected[0]["why"]

# order cap
placed, rejected = validate(S, [o("buy", s, 5) for s in ["AAPL", "MSFT", "META", "AMD", "CAT", "DE", "URI"]], cash, equity, held, False)
assert len(placed) == S.max_orders and len(rejected) == 2

# below minimum, and inputs not mutated
placed, rejected = validate(S, [o("buy", "AAPL", 0.5)], cash, equity, held, False)
assert rejected and held == {"IESC": 150.0, "NVDA": 50.0}

# sector cap: 60% of 500 = 300. tech holds NVDA 50; +100 AAPL, +100 MSFT fit (250), +100 META breaks it, blue collar unaffected
placed, rejected = validate(S, [o("buy", "AAPL", 100), o("buy", "MSFT", 100), o("buy", "META", 100), o("buy", "PWR", 100)], 500, equity, held, False)
assert [p.symbol for p in placed] == ["AAPL", "MSFT", "PWR"]
assert rejected[0]["symbol"] == "META" and "tech sector cap" in rejected[0]["why"]

# strategy overrides flow through: cautious has a 15% cap -> $75 on $500
C = STRATEGIES["cautious"]
placed, rejected = validate(C, [o("buy", "AAPL", 80), o("buy", "AAPL", 75)], cash, equity, {}, False)
assert [r["symbol"] for r in rejected] == ["AAPL"] and len(placed) == 1

# ---- guard: mechanical exits ----
def pos(sym, entry, price, qty=1.0):
    return P(symbol=sym, avg_entry_price=str(entry), current_price=str(price), market_value=str(price * qty), qty=str(qty))

# stop-loss at -8% from entry beats the trailing stop when there was never a peak
orders, peaks = guard_orders(S, [pos("NVDA", 100, 92)], 500, {})
assert len(orders) == 1 and orders[0].notional_usd == 92 and "stop-loss" in orders[0].reason
assert peaks == {"NVDA": 100}

# -7% holds on main, but trips cautious's 6% stop
assert guard_orders(S, [pos("NVDA", 100, 93)], 500, {})[0] == []
assert len(guard_orders(C, [pos("NVDA", 100, 93)], 500, {})[0]) == 1

# trailing: peaked at 150, now 135 -> sell; at 136 -> hold; peak only ever rises
orders, peaks = guard_orders(S, [pos("GOOGL", 100, 135, 0.5)], 500, {"GOOGL": 150})
assert len(orders) == 1 and "trailing" in orders[0].reason and peaks["GOOGL"] == 150
orders, peaks = guard_orders(S, [pos("GOOGL", 100, 136, 0.5)], 500, {"GOOGL": 150})
assert orders == [] and peaks["GOOGL"] == 150
_, peaks = guard_orders(S, [pos("GOOGL", 100, 160, 0.5)], 500, {"GOOGL": 150})
assert peaks["GOOGL"] == 160

# trim: $150 position on $550 equity, cap $110 -> sell $40
orders, _ = guard_orders(S, [pos("META", 100, 150)], 550, {})
assert len(orders) == 1 and orders[0].notional_usd == 40 and "trim" in orders[0].reason

# a closed position drops out of peaks
_, peaks = guard_orders(S, [pos("META", 100, 100)], 500, {"GOOGL": 150})
assert peaks == {"META": 100}

# ---- deposit-aware benchmark ledger ----
prices0 = {"A": 10.0, "B": 20.0}
led = apply_deposit({"contributed": 0.0, "units": {}, "seen": []}, 500, prices0)
assert led["contributed"] == 500 and led["units"] == {"A": 25.0, "B": 12.5}
assert benchmark_value(led, prices0) == 500
prices1 = {"A": 20.0, "B": 40.0}
assert benchmark_value(led, prices1) == 1000
led = apply_deposit(led, 200, prices1)
assert led["contributed"] == 700 and led["units"] == {"A": 30.0, "B": 15.0} and benchmark_value(led, prices1) == 1200
led2 = apply_deposit(led, -100, prices1)
assert led2["contributed"] == 600 and led2["units"] == led["units"] and led["contributed"] == 700

# ---- crypto strategy: own watchlist, single sector at 100%, symbol normalization ----
X = STRATEGIES["crypto"]
assert X.crypto and X.symbols == ["BTC/USD", "ETH/USD", "SOL/USD"] and X.canon("BTCUSD") == "BTC/USD" and X.canon("AAPL") == "AAPL"
# equities are off-limits; 40% cap on 500 = 200; sector cap 100% never binds
placed, rejected = validate(X, [o("buy", "AAPL", 50), o("buy", "BTC/USD", 200), o("buy", "ETH/USD", 200), o("buy", "SOL/USD", 100)], 500, 500, {}, False)
assert rejected[0]["symbol"] == "AAPL" and "watchlist" in rejected[0]["why"]
assert [p.symbol for p in placed] == ["BTC/USD", "ETH/USD", "SOL/USD"]
# guard sees positions as BTCUSD and reports them as BTC/USD; 12% stop
orders, peaks = guard_orders(X, [pos("BTCUSD", 80000, 70000, 0.002)], 500, {})
assert orders[0].symbol == "BTC/USD" and "stop-loss" in orders[0].reason and peaks == {"BTC/USD": 80000}
assert guard_orders(X, [pos("BTCUSD", 80000, 71000, 0.002)], 500, {})[0] == []

# ---- strategies.json shape ----
assert set(STRATEGIES) >= {"main", "luna", "momentum", "cautious", "crypto"}
assert all(isinstance(s, Strategy) and s.ends > "2026-09-14" for s in STRATEGIES.values())
assert STRATEGIES["main"].keys is not None, "main strategy must have ALPACA_API_KEY / ALPACA_SECRET_KEY"

print("ok")
