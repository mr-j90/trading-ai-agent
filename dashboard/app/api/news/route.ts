// Headlines for every configured strategy's watchlist, last 24h, newest first. Alpaca news (Benzinga).
import { DATA, alpaca, keysFor, strategies } from "../lib";

export const dynamic = "force-dynamic";

export async function GET() {
  const live = strategies().filter(keysFor);
  const headers = live.map(keysFor).find(Boolean);
  if (!headers) return Response.json({ items: [] });
  const symbols = [...new Set(live.flatMap((s) => Object.values(s.watchlist).flat()))];
  const flat = new Map(symbols.map((s) => [s.replace("/", ""), s])); // news tags crypto as BTCUSD
  const start = new Date(Date.now() - 24 * 3600e3).toISOString();
  const r = await alpaca<{ news: any[] }>(`${DATA}/v1beta1/news?symbols=${encodeURIComponent(symbols.join(","))}&start=${start}&limit=50&sort=desc&include_content=false`, headers);
  const items = r.news.map((n) => ({
    id: n.id,
    at: n.created_at,
    headline: n.headline,
    source: n.source,
    url: n.url,
    symbols: (n.symbols as string[]).map((x) => flat.get(x.replace("/", ""))).filter(Boolean),
  }));
  return Response.json({ items });
}
