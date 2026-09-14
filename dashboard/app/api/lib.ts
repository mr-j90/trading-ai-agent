// Server-only helpers shared by the API routes. Reads the agent's files; never writes trading state.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

export const ROOT = join(process.cwd(), "..");
export const TRADING = "https://paper-api.alpaca.markets";
export const DATA = "https://data.alpaca.markets";

export type StrategyCfg = {
  name: string; key_env: string; description?: string; model?: string; reasoning?: string; style?: string;
  start_equity?: number; max_position_pct?: number; max_sector_pct?: number; daily_stop_pct?: number; max_orders?: number;
  stop_loss_pct?: number; trailing_stop_pct?: number; ends?: string;
};

// mirrors the dataclass defaults in ../strategies.py
export const DEFAULTS = { model: "gpt-5.6-terra", reasoning: "low", start_equity: 500, max_position_pct: 0.2, max_sector_pct: 0.6, daily_stop_pct: 0.03, max_orders: 5, stop_loss_pct: 0.08, trailing_stop_pct: 0.1, ends: "2027-01-01" };

export function strategies(): StrategyCfg[] {
  const raw = JSON.parse(readFileSync(join(ROOT, "strategies.json"), "utf8")) as Record<string, Omit<StrategyCfg, "name">>;
  return Object.entries(raw).map(([name, cfg]) => ({ ...DEFAULTS, name, ...cfg }));
}

export function strategy(name: string): StrategyCfg {
  const s = strategies().find((x) => x.name === name);
  if (!s) throw new Error(`unknown strategy ${name}`);
  return s;
}

export const keysFor = (s: StrategyCfg) => {
  const k = process.env[`${s.key_env}_API_KEY`], sec = process.env[`${s.key_env}_SECRET_KEY`];
  return k && sec ? { "APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": sec } : null;
};

export async function alpaca<T>(url: string, headers: Record<string, string>): Promise<T> {
  const r = await fetch(url, { headers, cache: "no-store" });
  if (!r.ok) throw new Error(`${url} -> ${r.status} ${await r.text()}`);
  return r.json();
}

export const runDir = (name: string) => join(ROOT, "runs", name);

export function readJournal(name: string): any[] {
  const p = join(runDir(name), "journal.jsonl");
  if (!existsSync(p)) return [];
  return readFileSync(p, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

export function readLedger(name: string): { contributed: number; units: Record<string, number> } | null {
  const p = join(runDir(name), "benchmark.json");
  return existsSync(p) ? JSON.parse(readFileSync(p, "utf8")) : null;
}

export function readHalt(name: string): string | null {
  const p = join(runDir(name), "HALT");
  return existsSync(p) ? readFileSync(p, "utf8") : null;
}
