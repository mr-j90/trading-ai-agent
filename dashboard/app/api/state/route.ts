// Detail view for one strategy: journal files + its Alpaca paper account. Read-only.
import { WATCHLIST, SECTOR_OF } from "../../watchlist";
import { DATA, TRADING, alpaca, keysFor, readHalt, readJournal, readLedger, strategy } from "../lib";

export const dynamic = "force-dynamic";
const SCHEDULE_ET = ["09:45", "12:30", "15:30"]; // mirrors com.ies.trading-agent.plist

function nextRun(now: Date) {
  // ponytail: naive ET clock via Intl; ignores holidays (the agent's clock gate handles those)
  const et = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour12: false, weekday: "short", hour: "2-digit", minute: "2-digit" });
  for (let d = 0; d < 8; d++) {
    const day = new Date(now.getTime() + d * 864e5);
    const parts = Object.fromEntries(et.formatToParts(day).map((p) => [p.type, p.value]));
    if (["Sat", "Sun"].includes(parts.weekday)) continue;
    const hhmm = `${parts.hour}:${parts.minute}`;
    for (const slot of SCHEDULE_ET) if (d > 0 || slot > hhmm) return `${parts.weekday} ${slot} ET`;
  }
  return null;
}

export async function GET(req: Request) {
  const name = new URL(req.url).searchParams.get("strategy") ?? "main";
  const S = strategy(name);
  const headers = keysFor(S);
  if (!headers) return Response.json({ configured: false, strategy: S }, { status: 200 });

  const now = new Date();
  const journal = readJournal(name);
  const halt = readHalt(name);
  const ledger = readLedger(name);
  const symbols = Object.keys(SECTOR_OF).join(",");
  const [account, positions, daily, intraday, snapshots] = await Promise.all([
    alpaca<any>(`${TRADING}/v2/account`, headers),
    alpaca<any[]>(`${TRADING}/v2/positions`, headers),
    // Alpaca quirk: multi-day intraday history returns P&L deltas in `equity`; only 1D intraday returns real equity.
    // ponytail: period=3M covers the 8-week run; bump if it goes longer
    alpaca<any>(`${TRADING}/v2/account/portfolio/history?period=3M&timeframe=1D`, headers),
    alpaca<any>(`${TRADING}/v2/account/portfolio/history?period=1D&timeframe=15Min&intraday_reporting=market_hours`, headers),
    alpaca<Record<string, any>>(`${DATA}/v2/stocks/snapshots?symbols=${symbols}&feed=iex`, headers),
  ]);
  const points = (h: any): [number, number][] => (h.timestamp as number[]).map((t, i) => [t * 1000, h.equity[i]]);
  const last = (s: string) => snapshots[s]?.latestTrade?.p ?? snapshots[s]?.dailyBar?.c;
  const contributed = ledger?.contributed ?? S.start_equity!;
  const benchmarkValue = ledger ? Object.entries(ledger.units).reduce((sum, [s, u]) => sum + (last(s) ? u * last(s) : 0), 0) : null;

  const today = now.toISOString().slice(0, 10);
  const todayEntries = journal.filter((e) => e.ts.startsWith(today));
  const runStart = journal[0] ? new Date(journal[0].ts).setUTCHours(0, 0, 0, 0) : 0;
  return Response.json({
    configured: true,
    strategy: S,
    now: now.toISOString(),
    equity: +account.equity,
    cash: +account.buying_power,
    startOfDayEquity: todayEntries.find((e) => e.equity)?.equity ?? +account.last_equity,
    contributed,
    benchmarkValue,
    halt,
    lastRun: journal.at(-1)?.ts ?? null,
    lastError: journal.at(-1)?.error ?? null,
    nextRun: halt ? null : nextRun(now),
    curve: [...points(daily).filter(([t]) => t < (intraday.timestamp[0] ?? Infinity) * 1000), ...points(intraday)]
      .filter(([t, e]) => e != null && e > 0 && t >= runStart),
    positions: positions.map((p) => ({
      symbol: p.symbol,
      sector: SECTOR_OF[p.symbol] ?? "other",
      qty: +p.qty,
      marketValue: +p.market_value,
      price: +p.current_price,
      unrealizedPl: +p.unrealized_pl,
      unrealizedPlpc: +p.unrealized_plpc,
      dayChangePc: +p.change_today,
    })),
    watchlist: WATCHLIST,
    prices: Object.fromEntries(Object.keys(SECTOR_OF).map((s) => [s, { price: last(s), dayPc: snapshots[s]?.prevDailyBar?.c ? last(s) / snapshots[s].prevDailyBar.c - 1 : null }])),
    journal: journal.slice(-30).reverse(),
  });
}
