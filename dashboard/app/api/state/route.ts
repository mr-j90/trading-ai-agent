// Read-only. Reads the agent's journal files and the Alpaca paper account; never writes.
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { WATCHLIST, SECTOR_OF } from "../../watchlist";

export const dynamic = "force-dynamic";

const ROOT = join(process.cwd(), "..");
const TRADING = "https://paper-api.alpaca.markets";
const DATA = "https://data.alpaca.markets";
const headers = () => ({
  "APCA-API-KEY-ID": process.env.ALPACA_API_KEY!,
  "APCA-API-SECRET-KEY": process.env.ALPACA_SECRET_KEY!,
});
const SCHEDULE_ET = ["09:45", "12:30", "15:30"]; // mirrors com.ies.trading-agent.plist

async function alpaca<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: headers(), cache: "no-store" });
  if (!r.ok) throw new Error(`${url} -> ${r.status} ${await r.text()}`);
  return r.json();
}

function readJournal() {
  const p = join(ROOT, "journal.jsonl");
  if (!existsSync(p)) return [];
  return readFileSync(p, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

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

export async function GET() {
  const now = new Date();
  const journal = readJournal();
  const halt = existsSync(join(ROOT, "HALT")) ? readFileSync(join(ROOT, "HALT"), "utf8") : null;
  const benchmarkBase: Record<string, number> | null = existsSync(join(ROOT, "benchmark.json"))
    ? JSON.parse(readFileSync(join(ROOT, "benchmark.json"), "utf8"))
    : null;

  const symbols = Object.keys(SECTOR_OF).join(",");
  const [account, positions, daily, intraday, snapshots] = await Promise.all([
    alpaca<any>(`${TRADING}/v2/account`),
    alpaca<any[]>(`${TRADING}/v2/positions`),
    // Alpaca quirk: multi-day intraday history returns P&L deltas in `equity`; only 1D intraday returns real equity.
    // ponytail: period=3M covers the 8-week run; bump if it goes longer
    alpaca<any>(`${TRADING}/v2/account/portfolio/history?period=3M&timeframe=1D`),
    alpaca<any>(`${TRADING}/v2/account/portfolio/history?period=1D&timeframe=15Min&intraday_reporting=market_hours`),
    alpaca<Record<string, any>>(`${DATA}/v2/stocks/snapshots?symbols=${symbols}&feed=iex`),
  ]);
  const points = (h: any): [number, number][] => (h.timestamp as number[]).map((t, i) => [t * 1000, h.equity[i]]);

  const last = (s: string) => snapshots[s]?.latestTrade?.p ?? snapshots[s]?.dailyBar?.c;
  let benchmarkReturn: number | null = null;
  if (benchmarkBase) {
    const rets = Object.entries(benchmarkBase).filter(([s]) => last(s)).map(([s, p0]) => last(s) / p0 - 1);
    benchmarkReturn = rets.length ? rets.reduce((a, b) => a + b, 0) / rets.length : null;
  }

  const today = now.toISOString().slice(0, 10);
  const todayEntries = journal.filter((e) => e.ts.startsWith(today));
  // history includes the account before the $500 reset; keep only the run (from the first journal entry's day)
  const runStart = journal[0] ? new Date(journal[0].ts).setUTCHours(0, 0, 0, 0) : 0;
  return Response.json({
    now: now.toISOString(),
    equity: +account.equity,
    cash: +account.buying_power,
    startOfDayEquity: todayEntries[0]?.equity ?? +account.last_equity,
    benchmarkReturn,
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
