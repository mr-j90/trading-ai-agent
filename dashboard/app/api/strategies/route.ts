// Admin comparison: one row per strategy. Read-only.
import { TRADING, alpaca, keysFor, prices, readHalt, readJournal, readLedger, strategies } from "../lib";

export const dynamic = "force-dynamic";

export async function GET() {
  const all = strategies();
  const today = new Date().toISOString().slice(0, 10);
  const anyKeys = all.map(keysFor).find(Boolean);
  const clock = anyKeys ? await alpaca<{ is_open: boolean; next_open: string; next_close: string; timestamp: string }>(`${TRADING}/v2/clock`, anyKeys).catch(() => null) : null;

  const rows = await Promise.all(all.map(async (S) => {
    const headers = keysFor(S);
    const journal = readJournal(S.name);
    const ledger = readLedger(S.name);
    const halt = readHalt(S.name);
    const base = {
      name: S.name, description: S.description ?? "", model: S.model, assetClass: S.asset_class, ends: S.ends, halt,
      configured: !!headers, ended: today >= S.ends!,
      lastRun: journal.at(-1)?.ts ?? null, lastError: journal.at(-1)?.error ?? null,
      cycles: journal.filter((e) => (e.kind ?? "cycle") === "cycle" && !e.error).length,
      trades: journal.reduce((n, e) => n + e.placed.length, 0),
      guardExits: journal.filter((e) => e.kind === "guard").reduce((n, e) => n + e.placed.length, 0),
      rejected: journal.reduce((n, e) => n + e.rejected.length, 0),
      errors: journal.filter((e) => e.error).length,
      cost: journal.reduce((n, e) => n + (e.cost_usd ?? 0), 0),
    };
    if (!headers) return { ...base, equity: null, contributed: S.start_equity, benchmarkValue: null, positions: 0 };
    try {
      const [account, positions, px] = await Promise.all([
        alpaca<any>(`${TRADING}/v2/account`, headers), alpaca<any[]>(`${TRADING}/v2/positions`, headers), ledger ? prices(S, headers) : Promise.resolve({} as Record<string, { price: number }>),
      ]);
      const contributed = ledger?.contributed ?? S.start_equity;
      const benchmarkValue = ledger ? Object.entries(ledger.units).reduce((sum, [s, u]) => sum + (px[s]?.price ? u * px[s].price : 0), 0) : null;
      return { ...base, equity: +account.equity, dayChange: +account.equity / +account.last_equity - 1, contributed, benchmarkValue, positions: positions.length };
    } catch (e) {
      return { ...base, equity: null, contributed: S.start_equity, benchmarkValue: null, positions: 0, lastError: String(e) };
    }
  }));
  return Response.json({ now: new Date().toISOString(), clock, rows });
}
