// Keep in sync with ../watchlist.py (source of truth).
export const WATCHLIST: Record<string, string[]> = {
  tech: ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "AMD", "ORCL", "CRM", "PLTR", "ANET"],
  blue_collar: ["IESC", "PWR", "EME", "FIX", "MTZ", "DY", "STRL", "URI", "CAT", "DE", "FAST", "WSO"],
};
export const SECTOR_OF: Record<string, string> = Object.fromEntries(
  Object.entries(WATCHLIST).flatMap(([sector, syms]) => syms.map((s) => [s, sector])),
);
