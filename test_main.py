"""Self-check for the money path. Run: uv run python test_main.py"""

from main import MAX_ORDERS, Order, validate

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

print("ok")
