// Admin comparison: one row per strategy. Read-only.
import { SECTOR_OF } from "../../watchlist";
import { DATA, TRADING, alpaca, keysFor, readHalt, readJournal, readLedger, strategies } from "../lib";

export const dynamic = "force-dynamic";

export async function GET() {
  const all = strategies();
  const anyKeys = all.map(keysFor).find(Boolean);
  const snapshots = anyKeys
    ? await alpaca<Record<string, any>>(`${DATA}/v2/stocks/snapshots?symbols=${Object.keys(SECTOR_OF).join(",")}&feed=iex`, anyKeys)
    : {};
  const last = (s: string) => snapshots[s]?.latestTrade?.p ?? snapshots[s]?.dailyBar?.c;
  const today = new Date().toISOString().slice(0, 10);

  const rows = await Promise.all(all.map(async (S) => {
    const headers = keysFor(S);
    const journal = readJournal(S.name);
    const ledger = readLedger(S.name);
    const halt = readHalt(S.name);
    const base = {
      name: S.name, description: S.description ?? "", model: S.model, ends: S.ends, halt,
      configured: !!headers, ended: today >= S.ends!,
      lastRun: journal.at(-1)?.ts ?? null, lastError: journal.at(-1)?.error ?? null,
      cycles: journal.filter((e) => (e.kind ?? "cycle") === "cycle" && !e.error).length,
      trades: journal.reduce((n, e) => n + e.placed.length, 0),
      guardExits: journal.filter((e) => e.kind === "guard").reduce((n, e) => n + e.placed.length, 0),
      rejected: journal.reduce((n, e) => n + e.rejected.length, 0),
      errors: journal.filter((e) => e.error).length,
    };
    if (!headers) return { ...base, equity: null, contributed: S.start_equity, benchmarkValue: null, positions: 0 };
    try {
      const [account, positions] = await Promise.all([alpaca<any>(`${TRADING}/v2/account`, headers), alpaca<any[]>(`${TRADING}/v2/positions`, headers)]);
      const contributed = ledger?.contributed ?? S.start_equity!;
      const benchmarkValue = ledger ? Object.entries(ledger.units).reduce((sum, [s, u]) => sum + (last(s) ? u * last(s) : 0), 0) : null;
      return { ...base, equity: +account.equity, dayChange: +account.equity / +account.last_equity - 1, contributed, benchmarkValue, positions: positions.length };
    } catch (e) {
      return { ...base, equity: null, contributed: S.start_equity, benchmarkValue: null, positions: 0, lastError: String(e) };
    }
  }));
  return Response.json({ now: new Date().toISOString(), rows });
}
