# Alpaca market data and news on the free (IEX) plan — alpaca-py 0.44

Resolves GitHub issue #3. Sources: docs.alpaca.markets (fetched 2026-09-14) and the
alpaca-py 0.44.0 source installed in `.venv` (`alpaca/data/`, `alpaca/common/`).

## TL;DR

- Free ("Basic") plan = **IEX feed only**, real-time, ~2.5% of US volume. SIP (all exchanges)
  is queryable only with `end` >= 15 min in the past or via `feed=delayed_sip`. [1][3][4]
- Bars: any `TimeFrame` from `1Min` to `12Month`, history since 2016, up to 10,000 bars/page,
  many symbols per request. [1][2]
- News: one REST endpoint (`GET /v1beta1/news`), Benzinga-sourced, history to 2015,
  comma-separated `symbols` filter, 50 items/page max, `page_token` pagination (the SDK
  auto-pages). [5][6]
- Rate limit: **200 requests/min** on the free plan (10,000/min on Algo Trader Plus). A
  20-30 ticker watchlist polled a few times a day uses ~3 requests per poll if you batch
  symbols — irrelevant to the limit. [1]

## 1. Plan facts (free vs paid)

From the subscription comparison table [1]:

| | Basic (free) | Algo Trader Plus ($99/mo) |
|---|---|---|
| Equities real-time | IEX only | All US exchanges (SIP) |
| Historical data | "latest 15 minutes" restriction (SIP) | no restriction |
| Historical lookback | since 2016 | since 2016 |
| REST rate limit | 200 / min | 10,000 / min |
| Websocket symbols | 30 max | unlimited |

FAQ wording on the restriction [3]: querying recent SIP data without a subscription returns
`"subscription does not permit querying recent SIP data"`. Workarounds: `feed=iex`, or an
`end` at least 15 minutes in the past. IEX is "the only feed that can be used without a
subscription" and covers a single exchange, ~2.5% of market volume — the FAQ's AAPL example
for 2023-09-29 shows ~12.6k IEX trades vs ~535k SIP trades in the daily bar, so IEX
volume/trade-count fields are **not** market-wide and thin names may have sparse minute
bars. [3][4]

The `feed` default on `/stocks/bars/latest` and `/stocks/snapshots` is "sip if the user has
the unlimited subscription, otherwise iex" [7][8], so a free account that omits `feed` gets
IEX automatically. On `/stocks/bars` the `start`/`end` defaults are likewise "at least 15
minutes ago if the user doesn't have real-time access for the feed". [2]

## 2. Bars via alpaca-py

Classes (all verified in `.venv/.../alpaca/data/`):

- `alpaca.data.historical.StockHistoricalDataClient(api_key, secret_key, ...)` — base URL
  `https://data.alpaca.markets`, API version `v2` (`historical/stock.py`).
  - `get_stock_bars(StockBarsRequest) -> BarSet` -> `GET /v2/stocks/bars`, `page_size=10_000`.
  - `get_stock_latest_bar(StockLatestBarRequest) -> dict[str, Bar]` -> `/v2/stocks/bars/latest`
    (latest **minute** bar).
  - `get_stock_snapshot(StockSnapshotRequest) -> dict[str, Snapshot]` -> `/v2/stocks/snapshots`
    (latest trade, latest quote, minute bar, daily bar, previous daily bar in one call). [8]
- `alpaca.data.requests.StockBarsRequest(symbol_or_symbols, timeframe, start=None, end=None,
  limit=None, adjustment=None, feed=None, sort=None, asof=None, currency=None)`
  (`requests.py` lines 21-107). tz-aware `start`/`end` are converted to naive UTC; naive
  values are assumed UTC.
- `alpaca.data.timeframe.TimeFrame(amount, TimeFrameUnit)` with shortcuts `TimeFrame.Minute`,
  `.Hour`, `.Day`, `.Week`, `.Month`. SDK validation: Minute 1-59, Hour 1-23, Day/Week
  only 1, Month in {1,2,3,6,12} (`timeframe.py` `validate_timeframe`). The REST API also
  accepts `4Month`; the SDK rejects it — minor mismatch. [2]
- `alpaca.data.enums.DataFeed`: `IEX`, `SIP`, `DELAYED_SIP`, `OTC`, `BOATS`, `OVERNIGHT`.
  `Adjustment`: `RAW` (default), `SPLIT`, `DIVIDEND`, `ALL`.
- `BarSet.df` -> pandas DataFrame indexed by `(symbol, timestamp)` (`models/base.py`
  `TimeSeriesMixin.df`).

REST parameters behind it [2]: `symbols` comma-separated; `timeframe` `[1-59]Min`,
`[1-23]Hour`, `1Day`, `1Week`, `[1,2,3,4,6,12]Month`; `limit` default 1000, max 10,000 per
page; `sort` `asc`|`desc`; response `{bars: {SYMBOL: [{t,o,h,l,c,v,n,vw}]}, next_page_token}`.
Results are sorted by symbol then timestamp, so with several symbols one symbol can fill the
first page — the SDK handles this by following `next_page_token` until it is null
(`common/rest.py` `_get_marketdata`). Rate-limit headers `X-RateLimit-Limit`,
`X-RateLimit-Remaining`, `X-RateLimit-Reset` are returned. [2]

Delay: IEX data itself is real-time on the free plan; the "15-minute delay" applies only to
SIP. Bars are aggregated from trades with timestamps truncated to the minute/day. [3]

```python
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed, Adjustment

client = StockHistoricalDataClient(api_key, secret_key)  # keys from env, never in code
watchlist = ["AAPL", "MSFT", "NVDA"]  # 20-30 symbols is fine in one request

intraday = client.get_stock_bars(StockBarsRequest(
    symbol_or_symbols=watchlist,
    timeframe=TimeFrame(5, TimeFrameUnit.Minute),
    start=datetime.now(timezone.utc) - timedelta(days=5),
    feed=DataFeed.IEX,               # explicit; also the free-plan default
)).df

daily = client.get_stock_bars(StockBarsRequest(
    symbol_or_symbols=watchlist,
    timeframe=TimeFrame.Day,
    start=datetime(2024, 1, 1),
    adjustment=Adjustment.ALL,
    feed=DataFeed.IEX,
)).df
```

## 3. News via alpaca-py

- `alpaca.data.historical.NewsClient(api_key, secret_key, ...)` — API version `v1beta1`
  (`historical/news.py`). `get_news(NewsRequest) -> NewsSet` -> `GET /v1beta1/news` with
  `page_limit=50, page_size=50`.
- `alpaca.data.requests.NewsRequest(start=None, end=None, sort=None, symbols=None,
  limit=None, include_content=None, exclude_contentless=None, page_token=None)`
  (`requests.py` lines 527-551). Note `symbols` is a **comma-separated `str`**, not a list;
  `sort` is a plain `str` (`"asc"`/`"desc"`).
- `alpaca.data.models.news.News` fields: `id, headline, source, url, summary, created_at,
  updated_at, symbols, author, content, images`. `NewsSet.data["news"]` is the list;
  `NewsSet.df` also works.
- Live alternative: `alpaca.data.live.NewsDataStream` -> `wss://stream.data.alpaca.markets/v1beta1/news`,
  subscribe with symbols or `"*"`. [9]

REST semantics [5][6]:

- Source: "All news data is currently provided directly by Benzinga"; history "dating back to
  2015"; ~130+ articles/day across the whole feed.
- `symbols`: comma-separated list (e.g. `AAPL,TSLA`) — a single per-watchlist query works.
- `limit`: 1-50 per page (default 10). `sort`: by `updated_at`, default `desc`.
- `start`/`end`: inclusive, RFC-3339 or `YYYY-MM-DD`; default `start` = beginning of the current
  day, default `end` = now ("otherwise 15 minutes before the current time" if no real-time
  access — the reference reuses the market-data wording; whether news is actually held back
  for free accounts is not stated separately, so verify empirically before relying on it).
- `include_content=true` returns the article body (may contain HTML); `exclude_contentless=true`
  drops headline-only items.
- Pagination: `next_page_token` -> `page_token`. **The SDK loops pages automatically until
  `next_page_token` is null** (`_get_marketdata`). If `NewsRequest.limit` is unset, that means
  it fetches *every* article in the window, 50 per request — set `limit` to cap request count.

```python
from alpaca.data.historical import NewsClient
from alpaca.data.requests import NewsRequest

news = NewsClient(api_key, secret_key).get_news(NewsRequest(
    symbols=",".join(watchlist),
    start=datetime.now(timezone.utc) - timedelta(days=1),
    limit=50,                 # caps total items, and therefore requests (50/page)
    include_content=False,
    sort="desc",
))
for a in news.data["news"]:
    print(a.updated_at, a.symbols, a.headline)
```

## 4. Rate limits for a 20-30 ticker watchlist

Budget: 200 requests/min on the free plan. [1]

| Poll design | Requests per poll |
|---|---|
| Batched: 1 bars call (all symbols) + 1 snapshot call + 1 news call (limit<=50) | ~3 |
| Bars with 25 symbols x 390 one-minute bars = 9,750 rows | still 1 page (10k max) |
| Naive per-ticker loop: 25 bars + 25 news | 50 — still under 200/min if run in one burst |

Polling a few times a day is nowhere near the limit even unbatched. The only ways to hit it:
un-`limit`ed news over a long window (SDK auto-pages at 50/request), or backfilling years of
minute bars in a tight loop (10k rows/request, e.g. ~1 year of 1-min bars for one ticker is
~10 requests).

SDK behaviour on 429: `RESTClient` retries `DEFAULT_RETRY_ATTEMPTS = 3` times with a fixed
`DEFAULT_RETRY_WAIT_SECONDS = 3` sleep on status codes `[429, 504]`, then raises `APIError`
(`common/rest.py` `_request`/`_one_request`, `common/constants.py`). `StockHistoricalDataClient`
and `NewsClient` constructors do **not** expose `retry_attempts`/`retry_wait_seconds`; to
change them set `client._retry` / `client._retry_wait` after construction, or honour
`X-RateLimit-Remaining` yourself.

## 5. Practical recommendations for this project

1. Always pass `feed=DataFeed.IEX` explicitly so a future paid upgrade doesn't silently change
   semantics, and remember IEX volume is a fraction of consolidated volume.
2. Batch the watchlist into one `StockBarsRequest` / one `NewsRequest`; don't loop per ticker.
3. Always set `NewsRequest.limit`.
4. Use `get_stock_snapshot` for "what's the price now + today's bar" — one request, IEX default.
5. If sub-15-minute consolidated (SIP) prices ever matter, that is the $99/mo Algo Trader Plus
   plan; `DataFeed.DELAYED_SIP` is the free middle ground.

## Sources

1. Alpaca, "About Market Data API" — subscription comparison table.
   https://docs.alpaca.markets/docs/about-market-data-api
2. Alpaca API reference, `GET /v2/stocks/bars`. https://docs.alpaca.markets/reference/stockbars
3. Alpaca, "Market Data FAQ" — IEX vs SIP, subscription error, bar aggregation.
   https://docs.alpaca.markets/docs/market-data-faq
4. Alpaca, "Historical Stock Data" — feed descriptions.
   https://docs.alpaca.markets/docs/historical-stock-data-1
5. Alpaca, "Historical News Data" — Benzinga, 2015 lookback, volume.
   https://docs.alpaca.markets/docs/historical-news-data
6. Alpaca API reference, `GET /v1beta1/news`. https://docs.alpaca.markets/reference/news-3
7. Alpaca API reference, `GET /v2/stocks/bars/latest`. https://docs.alpaca.markets/reference/stocklatestbars-1
8. Alpaca API reference, `GET /v2/stocks/snapshots`. https://docs.alpaca.markets/reference/stocksnapshots-1
9. Alpaca, "Streaming Real-Time News". https://docs.alpaca.markets/docs/streaming-real-time-news
10. alpaca-py 0.44.0 source: `alpaca/data/historical/{stock,news}.py`, `alpaca/data/requests.py`,
    `alpaca/data/enums.py`, `alpaca/data/timeframe.py`, `alpaca/data/models/{news,base}.py`,
    `alpaca/common/{rest,constants}.py`. https://github.com/alpacahq/alpaca-py
