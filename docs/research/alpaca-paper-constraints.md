# Alpaca paper-account constraints for a $500 budget — alpaca-py 0.44

Resolves GitHub issue #2. Sources: docs.alpaca.markets (fetched 2026-09-14), the alpaca-py
0.44.0 source installed in `.venv` (`alpaca/trading/requests.py`, `enums.py`, `models.py`),
and a read-only probe of this project's paper account (`get_account`,
`get_account_configurations`, `get_asset` for all 24 watchlist tickers) run the same day.

## TL;DR

- **The $500 cap is already done at the account level.** The live probe shows the paper
  account at `cash=500`, `equity=500`, `buying_power=500`, `multiplier=1`,
  `shorting_enabled=False`. Alpaca lets you open a paper account "with arbitrary amount as
  you configure" [1]. Read `buying_power` from `get_account()` as the source of truth; do not
  build a parallel ledger. [1][P]
- **Notional market orders work.** `MarketOrderRequest(symbol, notional=25.0, side=BUY,
  time_in_force=DAY)`; `qty` and `notional` are mutually exclusive (SDK validator). Minimum is
  **$1**, up to 9 decimal places. Fractional trading is on for all accounts "in both live and
  paper environments". [2][3][S1]
- **PDT is gone, not merely unsimulated.** FINRA replaced the pattern-day-trader rule with the
  Intraday Margin Rule; Alpaca states "The 'pattern day trader' designation has been abolished"
  and "The previous '4-trade limit' has been eliminated." alpaca-py 0.44 marks
  `pattern_day_trader`, `daytrade_count`, `daytrading_buying_power`, `pdt_check`, `dtbp_check`
  as "Deprecated; removed from Alpaca responses on 2026-07-06" — the probe returns `None` for
  all of them. 3 cycles/day is unconstrained by trade counting. [4][5][S3][P]
- **Fractional/notional orders are DAY-only.** Market, limit, stop, stop-limit accept fractional
  qty; every one of them only with `time_in_force=day`. `notional` is narrower still: "Can only
  work for market order types and day for time in force." No GTC/IOC/FOK/OPG/CLS. Notional
  orders cannot be replaced (cancel + resubmit). [3][6][7]
- **All 24 watchlist tickers are `fractionable=True`, including IESC** (NASDAQ). Eligibility
  is per-asset, not per-cap-tier; check `get_asset(sym).fractionable` before ordering. [2][P]

## 1. Capping spend at $500

Alpaca's paper-trading page [1]:

> "Your initial paper trading account is created with $100k balance as a default setting.
> You can reset the paper trading account at any time later with arbitrary amount as you
> configure."
>
> "You cannot change the account balance after it is created, unless you reset it."
>
> "We've updated the dashboard to allow you to create and delete paper accounts, rather than
> resetting them. To create a new paper account, click the paper account number in the upper
> left corner of the dashboard and select 'Open New Paper Account.'"

This has already been done for this project. Probe output (2026-09-14) [P]:

```
status = ACTIVE   cash = 500   equity = 500   buying_power = 500
regt_buying_power = 500   non_marginable_buying_power = 500
multiplier = 1   shorting_enabled = False   trading_blocked = False
config: fractional_trading=True  max_margin_multiplier='4'  no_shorting=False
        pdt_check=None  dtbp_check=None
```

`multiplier = 1` means "buying_power = cash" [8][S3], so Alpaca itself refuses anything the
$500 does not cover and there is no margin leakage. Note the *configuration* still allows
`max_margin_multiplier='4'`; the effective multiplier is 1 because equity is under $2,000
("Accounts with $2,000 or more equity will have access to margin trading" [9]). If the
paper balance is ever raised to >= $2k, set `max_margin_multiplier="1"` via
`set_account_configurations` or the cap silently becomes $4k intraday.

Recommendation (own accounting vs reset): **reset wins**. Own accounting would have to
replicate open-order reservations ("your available buying power is reduced by existing open
buy long and sell short orders" [6]), partial fills (paper fills "partial ... for a random
size 10% of the time" [1]) and P&L drift. The API already does all of that. The only
code-side guard worth having is per-cycle: `notional = min(target, float(acct.buying_power))`
and skip the order if that is below $1.

Not simulated in paper, per [1]: market impact, information leakage, latency slippage, queue
position for non-marketable limits, price improvement, regulatory fees, dividends, borrow
fees. Fills are "matched against the best available current market price (NBBO)" and
"filled only when they become marketable" — so a notional market order on a liquid name
fills essentially at the ask.

## 2. Fractional / notional orders in alpaca-py 0.44

`alpaca/trading/requests.py` [S1]:

```python
class OrderRequest(NonEmptyRequest):
    qty: Optional[float] = None       # "Fractional qty for stocks only with market orders."
    notional: Optional[float] = None  # "For stocks, only works with MarketOrders. **Does not work with qty**."
    ...
    @model_validator(mode="before")
    def root_validator(cls, values):
        if not qty_set and not notional_set:
            raise ValueError("At least one of qty or notional must be provided")
        elif qty_set and notional_set:
            raise ValueError("Both qty and notional can not be set.")
```

`MarketOrderRequest` forces `type=OrderType.MARKET` and rejects any price field
(`limit_price`, `stop_price`, `trail_*`) with `ValueError`. So the agent's order is:

```python
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

client.submit_order(MarketOrderRequest(
    symbol="NVDA", notional=20.83, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
))
```

Server-side rules from the fractional-trading page [2] and `POST /v2/orders` reference [3]:

| Rule | Source text |
|---|---|
| Minimum | "You can now buy as little as $1 worth of shares for over 2,000 US equities" [2] |
| Precision | "Both notional and qty fields can take up to 9 decimal point values." [2] |
| `qty` | "number of shares to trade. Can be fractionable for only market and day order types." [3] |
| `notional` | "dollar amount to trade. Cannot work with `qty`. Can only work for market order types and day for time in force." [3] |
| Paper | "By default all Alpaca accounts are allowed to trade fractional shares in both live and paper environments." [2] |
| Shorting | "We do not support short sales in fractional orders. All fractional sell orders are marked long." [2] |
| Replace | "Notional orders (orders placed using the `notional` field instead of `qty`) cannot be replaced." [6] |
| Fill fields | `Order.filled_qty` / `filled_avg_price` are `Optional[Union[str, float]]`; read them post-fill to learn what $X actually bought. [S3] |

Sizing implication: $500 / 24 tickers is ~$20.83 per name if fully deployed at once; with
3 cycles a day the agent should size from live `buying_power`, not from a fixed $500 / N.

## 3. Pattern-day-trader rule: does paper simulate it?

Moot. Alpaca's current docs describe the FINRA change [4][5][10]:

> "The Intraday Margin Rule is a FINRA-mandated framework that replaces the legacy Pattern
> Day Trader (PDT) system" [5]
>
> Q: Is the Pattern Day Trader (PDT) rule still in effect? "No. The 'pattern day trader'
> designation has been abolished." [4]
>
> Q: Does the new rule limit the number of trades I can make? "No. The previous '4-trade
> limit' has been eliminated." [4]
>
> Q: Is the $25,000 minimum equity requirement still required? "No. The specific $25,000
> minimum previously required to maintain 'Pattern Day Trader' status has been removed." [4]

Comparison table from [5]: Trade limits — legacy "Max 3 day trades per 5 days (under $25k)"
vs new "Unlimited day trades"; buying power — legacy "Fixed (based on previous day's close)"
vs new "Dynamic (updates in real-time)". Violations are now *intraday margin deficits*
(a margin call to be met "within two business days", 90-day freeze if unmet), with a de
minimis carve-out: "An intraday margin call is generally not triggered if the unmet deficit
is less than $1,000 or 5% of account equity" [5]. A `multiplier=1`, long-only, cash-covered
account cannot incur a deficit at all.

alpaca-py 0.44 `models.py` [S3] confirms the API side:

> `daytrade_count` ... `pattern_day_trader` ... `daytrading_buying_power` ... `dtbp_check`
> ... `pdt_check`: "Deprecated; removed from Alpaca responses on 2026-07-06 (FINRA
> intraday-margin migration) and defaults to None."

The probe returned `None` for all five [P]. Do not branch on them.

Residual constraint on "several times a day": none from trade counting. The real limits are
(a) `buying_power`, which already nets open orders; (b) the fractional sell-sequence rule —
outside market hours you cannot stack a notional sell and a qty sell on the same symbol
(rejected with "unable to open new notional orders while having open closing position
orders") [6]; irrelevant while the agent trades only during RTH with DAY orders; (c) the
user-protection wash-trade check, which rejects an order that would cross your own resting
order on the same symbol — also applied in paper [11]. Cancel any resting DAY order on a
symbol before re-entering it in the next cycle.

## 4. Allowed order types and TIF for fractional shares

From the "Order Types vs Supported Time in Force" tables on the orders page [6]:

| Fractional orders (USD) | day | gtc | ioc | fok | opg | cls |
|---|---|---|---|---|---|---|
| market | Y | N | N | N | N | N |
| limit | Y | N | N | N | N | N |
| stop | Y | N | N | N | N | N |
| stop_limit | Y | N | N | N | N | N |

Whole-share orders, for contrast, accept day+gtc on all four types and ioc/fok/opg/cls on
market/limit [6]. `trailing_stop` is whole-share only (it does not appear in the fractional
table). The fractional page's own wording: "Alpaca currently supports fractional trading for
market, limit, stop & stop limit orders with a time in force=Day" [2]. `notional` narrows
that to **market only** [3][S1].

alpaca-py does not enforce the TIF restriction client-side — `TimeInForce` lists
`day, gtc, opg, cls, ioc, fok` for equities [S2] and `OrderRequest` accepts any of them with
`notional`; the API rejects the bad combination. The project's decision (MARKET + DAY +
notional) is exactly the one combination that is valid everywhere.

Extended hours: "Only limit orders with `time_in_force` set to `day` or `gtc` orders are
accepted as extended hours eligible" [6] — so notional market orders are RTH-only. A DAY
market order submitted after 4:00pm ET "is queued and submitted the following trading day"
[S2]; gate cycles on `get_clock().is_open` (see `macos-scheduling.md`).

## 5. Are fractional shares available for all US equities, including IESC?

No blanket guarantee — eligibility is a per-asset flag: "please make sure you query assets
details to check for the parameter `fractionable = true`" [2]; coverage is "over 2,000 US
equities" [2], and only exchange-listed names are eligible for extended-hours fractional
trading [2]. `Asset.fractionable: bool` is a required field on the model, alongside
`tradable`, `marginable`, `min_order_size`, `min_trade_increment`, `price_increment` [S3].

Probe of every watchlist ticker (2026-09-14) [P]:

| Ticker | fractionable | tradable | exchange | | Ticker | fractionable | tradable | exchange |
|---|---|---|---|---|---|---|---|---|
| AAPL | True | True | NASDAQ | | IESC | True | True | NASDAQ |
| AMD | True | True | NASDAQ | | META | True | True | NASDAQ |
| AMZN | True | True | NASDAQ | | MSFT | True | True | NASDAQ |
| ANET | True | True | NYSE | | MTZ | True | True | NYSE |
| AVGO | True | True | NASDAQ | | NVDA | True | True | NASDAQ |
| CAT | True | True | NYSE | | ORCL | True | True | NYSE |
| CRM | True | True | NYSE | | PLTR | True | True | NASDAQ |
| DE | True | True | NYSE | | PWR | True | True | NYSE |
| DY | True | True | NYSE | | STRL | True | True | NASDAQ |
| EME | True | True | NYSE | | URI | True | True | NYSE |
| FAST | True | True | NASDAQ | | WSO | True | True | NYSE |
| FIX | True | True | NYSE | | GOOGL | True | True | NASDAQ |

All 24 are fractionable; `min_order_size` / `min_trade_increment` / `price_increment` are
`None` for every one (those fields are populated for crypto). Flags can change, so a
startup check that filters the watchlist on `fractionable and tradable` costs one
`get_all_assets(GetAssetsRequest(asset_class=US_EQUITY))` call per run.

## Sources

Docs (all fetched 2026-09-14; append `.md` to any docs.alpaca.markets URL for the raw page):

- [1] Paper Trading — https://docs.alpaca.markets/docs/paper-trading
- [2] Fractional Trading — https://docs.alpaca.markets/docs/fractional-trading
- [3] POST /v2/orders reference (`qty`, `notional`, `time_in_force`, `extended_hours`) — https://docs.alpaca.markets/reference/postorder
- [4] Intraday Margin Rule for Non-Leverage Margin Accounts — https://docs.alpaca.markets/docs/intraday-margin-rule-for-non-leverage-margin-accounts
- [5] The Intraday Margin Rule — https://docs.alpaca.markets/docs/the-intraday-margin-rule
- [6] Orders at Alpaca (TIF tables, extended hours, fractional sell sequence, notional replace) — https://docs.alpaca.markets/docs/orders-at-alpaca
- [7] Account Configurations (`fractional_trading`, `max_margin_multiplier`, `no_shorting`) — https://docs.alpaca.markets/reference/patchaccountconfig-1
- [8] GET /v2/account reference (`buying_power`, `multiplier` definitions) — https://docs.alpaca.markets/reference/getaccount-1
- [9] Account Plans ("All accounts are opened as margin accounts. Accounts with $2,000 or more equity will have access to margin trading") — https://docs.alpaca.markets/docs/account-plans
- [10] Understanding FINRA's New Intraday Margin Rule and the End of PDT — https://docs.alpaca.markets/docs/understanding-finras-new-intraday-margin-rule-and-the-end-of-pdt
- [11] User Protection (wash-trade prevention, applies to paper) — https://docs.alpaca.markets/docs/user-protection

alpaca-py 0.44.0 source (`.venv/lib/python3.14/site-packages/alpaca/trading/`):

- [S1] `requests.py` — `OrderRequest` (lines 333-420), `MarketOrderRequest` (436-470), `ReplaceOrderRequest` (283)
- [S2] `enums.py` — `TimeInForce` (228-250), `OrderType` (121-136), `PDTCheck`/`DTBPCheck` (342-370)
- [S3] `models.py` — `Asset` (32-68), `Order` (168-231), `TradeAccount` (477-560), `AccountConfiguration` (564-590)

Live probe:

- [P] `TradingClient(paper=True).get_account()`, `.get_account_configurations()`, `.get_asset(sym)` for each ticker in `watchlist.py`, run 2026-09-14 against this project's paper account. Values will drift once the agent trades; re-run rather than trusting the tables above.
