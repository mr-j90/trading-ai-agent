"""Self-check for the money path. Run: uv run python test_main.py"""

from types import SimpleNamespace as P

from main import MAX_ORDERS, Order, guard_orders, validate

equity, cash = 500.0, 300.0
held = {"IESC": 150.0, "NVDA": 50.0}


def o(side, sym, usd):
    return Order(symbol=sym, side=side, notional_usd=usd, reason="t")


# cap: 20% of 500 = 100. NVDA holds 50, so +60 breaks it, +50 fits exactly.
placed, rejected = validate([o("buy", "NVDA", 60), o("buy", "NVDA", 50)], cash, equity, held, False)
assert [r["symbol"] for r in rejected] == ["NVDA"] and "cap" in rejected[0]["why"]
assert len(placed) == 1 and placed[0].notional_usd == 50

# cash runs out across a cycle
placed, rejected = validate([o("buy", "AAPL", 100), o("buy", "MSFT", 100), o("buy", "META", 100), o("buy", "AMD", 100)], cash, equity, held, False)
assert len(placed) == 3 and "cash" in rejected[0]["why"]

# sells: cannot exceed holding; proceeds don't fund same-cycle buys
placed, rejected = validate([o("sell", "IESC", 200), o("sell", "IESC", 150), o("buy", "CAT", 320)], cash, equity, held, False)
assert [r["symbol"] for r in rejected] == ["IESC", "CAT"]
assert placed[0].notional_usd == 150

# loss stop blocks buys, not sells
placed, rejected = validate([o("buy", "AAPL", 10), o("sell", "NVDA", 10)], cash, equity, held, True)
assert len(placed) == 1 and placed[0].side == "sell" and "loss stop" in rejected[0]["why"]

# order cap
placed, rejected = validate([o("buy", s, 5) for s in ["AAPL", "MSFT", "META", "AMD", "CAT", "DE", "URI"]], cash, equity, held, False)
assert len(placed) == MAX_ORDERS and len(rejected) == 2

# below minimum, and inputs not mutated
placed, rejected = validate([o("buy", "AAPL", 0.5)], cash, equity, held, False)
assert rejected and held == {"IESC": 150.0, "NVDA": 50.0}

# sector cap: 60% of 500 = 300. tech holds NVDA 50; +100 AAPL, +100 MSFT fit (250), +100 META breaks it, blue collar unaffected
placed, rejected = validate([o("buy", "AAPL", 100), o("buy", "MSFT", 100), o("buy", "META", 100), o("buy", "PWR", 100)], 500, equity, held, False)
assert [p.symbol for p in placed] == ["AAPL", "MSFT", "PWR"]
assert rejected[0]["symbol"] == "META" and "tech sector cap" in rejected[0]["why"]

# ---- guard: mechanical exits ----
def pos(sym, entry, price, qty=1.0):
    return P(symbol=sym, avg_entry_price=str(entry), current_price=str(price), market_value=str(price * qty), qty=str(qty))

# stop-loss at -8% from entry beats the trailing stop when there was never a peak
orders, peaks = guard_orders([pos("NVDA", 100, 92)], 500, {})
assert len(orders) == 1 and orders[0].notional_usd == 92 and "stop-loss" in orders[0].reason
assert peaks == {"NVDA": 100}

# -7% holds
orders, _ = guard_orders([pos("NVDA", 100, 93)], 500, {})
assert orders == []

# trailing: peaked at 150, now 135 -> sell; at 136 -> hold; peak only ever rises
orders, peaks = guard_orders([pos("GOOGL", 100, 135, 0.5)], 500, {"GOOGL": 150})
assert len(orders) == 1 and "trailing" in orders[0].reason and peaks["GOOGL"] == 150
orders, peaks = guard_orders([pos("GOOGL", 100, 136, 0.5)], 500, {"GOOGL": 150})
assert orders == [] and peaks["GOOGL"] == 150
_, peaks = guard_orders([pos("GOOGL", 100, 160, 0.5)], 500, {"GOOGL": 150})
assert peaks["GOOGL"] == 160

# trim: $150 position on $550 equity, cap $110 -> sell $40
orders, _ = guard_orders([pos("META", 100, 150)], 550, {})
assert len(orders) == 1 and orders[0].notional_usd == 40 and "trim" in orders[0].reason

# a closed position drops out of peaks
_, peaks = guard_orders([pos("META", 100, 100)], 500, {"GOOGL": 150})
assert peaks == {"META": 100}

print("ok")
