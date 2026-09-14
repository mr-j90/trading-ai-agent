"use client";
import { useEffect, useState } from "react";

type Position = { symbol: string; sector: string; qty: number; marketValue: number; price: number; unrealizedPl: number; unrealizedPlpc: number; dayChangePc: number };
type Entry = { ts: string; kind?: string; equity: number; market_view: string; placed: any[]; rejected: any[]; error: string | null; tokens_in?: number; tokens_out?: number; cost_usd?: number };
type StrategyCfg = { name: string; description?: string; model: string; reasoning: string; max_position_pct: number; max_sector_pct: number; daily_stop_pct: number; max_orders: number; stop_loss_pct: number; trailing_stop_pct: number; ends: string; key_env: string };
type State = {
  configured: boolean; strategy: StrategyCfg;
  now: string; equity: number; cash: number; startOfDayEquity: number; contributed: number; benchmarkValue: number | null; halt: string | null;
  lastRun: string | null; lastError: string | null; nextRun: string | null; curve: [number, number][];
  positions: Position[]; watchlist: Record<string, string[]>; prices: Record<string, { price: number; dayPc: number | null }>; journal: Entry[];
};
type Row = {
  name: string; description: string; model: string; ends: string; halt: string | null; configured: boolean; ended: boolean;
  lastRun: string | null; lastError: string | null; cycles: number; trades: number; guardExits: number; rejected: number; errors: number; cost: number;
  equity: number | null; dayChange?: number; contributed: number; benchmarkValue: number | null; positions: number;
};

const POLL_MS = 15_000;
const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const pct = (n: number | null, d = 2) => (n == null ? "–" : `${n >= 0 ? "+" : ""}${(n * 100).toFixed(d)}%`);
const tone = (n: number | null) => (n == null ? "" : n >= 0 ? "up" : "down");
const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });

export default function Page() {
  const [sel, setSel] = useState<string>(() => { try { return location.hash.slice(1) || "main"; } catch { return "main"; } });
  const [rows, setRows] = useState<Row[] | null>(null);
  const [s, setS] = useState<State | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [runOut, setRunOut] = useState<RunResult | null>(null);
  const tick = (name = sel) =>
    Promise.all([
      fetch("/api/strategies").then(async (r) => { if (!r.ok) throw new Error(await r.text()); setRows((await r.json()).rows); }),
      fetch(`/api/state?strategy=${name}`).then(async (r) => { if (!r.ok) throw new Error(await r.text()); setS(await r.json()); }),
    ]).then(() => setErr(null)).catch((e) => setErr(String(e)));
  useEffect(() => {
    setS(null); tick(sel);
    try { location.hash = sel; } catch {}
    const id = setInterval(() => tick(sel), POLL_MS);
    return () => clearInterval(id);
  }, [sel]);
  const run = async (job: "cycle" | "guard", name = sel) => {
    if (job === "cycle" && !confirm(`Run a decision cycle for "${name}" now? The model may place paper orders.`)) return;
    setRunning(`${name}:${job}`); setRunOut(null);
    try {
      const r = await fetch("/api/run", { method: "POST", body: JSON.stringify({ job, strategy: name }) });
      const j = await r.json();
      setRunOut({ ...parseRun(job, j.output), strategy: name });
    } catch (e) { setRunOut({ job, strategy: name, text: String(e), at: new Date().toISOString() }); }
    setRunning(null); tick();
  };
  const setHalt = async (name: string, halt: boolean) => {
    if (halt && !confirm(`Halt "${name}"? Scheduled runs will skip it until resumed.`)) return;
    await fetch("/api/halt", { method: "POST", body: JSON.stringify({ strategy: name, halt }) });
    tick();
  };

  if (!rows) return <main className="wrap"><p className="muted">{err ?? "loading…"}</p></main>;
  const cur = rows.find((r) => r.name === sel);
  const busy = (name: string) => !!running || !!rows.find((r) => r.name === name)?.halt || !rows.find((r) => r.name === name)?.configured;

  return (
    <main className="wrap">
      <header className="bar">
        <span><span className={`dot ${cur?.halt ? "halted" : cur?.configured ? "live" : ""}`} /> trading agents · paper · {rows.filter((r) => r.configured && !r.halt).length}/{rows.length} live</span>
        <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>updated {hhmm(new Date().toISOString())}{err ? ` · refresh failed: ${err}` : ""}</span>
      </header>
      {runOut && <RunOutput r={runOut} onClose={() => setRunOut(null)} />}

      <section className="card admin">
        <div className="row"><div className="label">Strategies</div><span className="muted small">runs until each strategy's end date · click a row for detail</span></div>
        <div className="scroll">
          <table>
            <thead><tr>
              <th>strategy</th><th>model</th><th>status</th><th className="r">equity</th><th className="r">today</th><th className="r">return</th><th className="r">vs bench</th>
              <th className="r">pos</th><th className="r">cycles</th><th className="r">trades</th><th className="r">guard</th><th className="r">rejected</th><th className="r">errors</th><th className="r">model $</th><th>last run</th><th></th>
            </tr></thead>
            <tbody>
              {rows.map((r) => {
                const ret = r.equity == null ? null : r.equity / r.contributed - 1;
                const bench = r.benchmarkValue == null ? null : r.benchmarkValue / r.contributed - 1;
                const gap = ret == null || bench == null ? null : ret - bench;
                const status = !r.configured ? "not configured" : r.halt ? "halted" : r.ended ? "ended" : "live";
                return (
                  <tr key={r.name} className={`${r.name === sel ? "selected" : ""} ${r.configured ? "" : "dim"}`} onClick={() => setSel(r.name)} title={r.description}>
                    <td><b>{r.name}</b><div className="muted small desc">{r.description}</div></td>
                    <td className="muted">{r.model}</td>
                    <td><span className={`badge ${status.replace(" ", "-")}`}>{status}</span></td>
                    <td className="r">{r.equity == null ? "–" : usd(r.equity)}</td>
                    <td className={`r ${tone(r.dayChange ?? null)}`}>{pct(r.dayChange ?? null)}</td>
                    <td className={`r ${tone(ret)}`}>{pct(ret)}</td>
                    <td className={`r ${tone(gap)}`}>{gap == null ? "–" : `${(gap * 100).toFixed(1)} pts`}</td>
                    <td className="r">{r.positions}</td><td className="r">{r.cycles}</td><td className="r">{r.trades}</td><td className="r">{r.guardExits}</td>
                    <td className="r">{r.rejected}</td><td className={`r ${r.errors ? "down" : ""}`}>{r.errors}</td>
                    <td className="r muted" title="model spend, all time, from token usage">{r.cost ? `$${r.cost.toFixed(2)}` : "–"}</td>
                    <td className="muted">{r.lastRun ? new Date(r.lastRun).toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "never"}</td>
                    <td className="actions" onClick={(e) => e.stopPropagation()}>
                      <button onClick={() => run("cycle", r.name)} disabled={busy(r.name)} title="run a decision cycle now">{running === `${r.name}:cycle` ? "…" : "▶"}</button>
                      <button onClick={() => run("guard", r.name)} disabled={busy(r.name)} title="run the guard now">{running === `${r.name}:guard` ? "…" : "guard"}</button>
                      {r.halt ? <button onClick={() => setHalt(r.name, false)} disabled={!r.configured}>resume</button> : <button onClick={() => setHalt(r.name, true)} disabled={!r.configured}>halt</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {!s ? <p className="muted">loading {sel}…</p> : !s.configured ? (
        <section className="card">
          <div className="label">{sel} · not configured</div>
          <p className="muted">This strategy needs its own Alpaca paper account. Create one in the Alpaca dashboard, reset it to $500, then add to <code>.env</code>:</p>
          <pre>{`${s.strategy.key_env}_API_KEY=\n${s.strategy.key_env}_SECRET_KEY=`}</pre>
          <p className="muted small">{s.strategy.description}</p>
        </section>
      ) : <Detail s={s} sel={sel} />}
    </main>
  );
}

function Detail({ s, sel }: { s: State; sel: string }) {
  const sinceStart = s.equity / s.contributed - 1;
  const day = s.equity / s.startOfDayEquity - 1;
  const benchRet = s.benchmarkValue == null ? null : s.benchmarkValue / s.contributed - 1;
  const gap = benchRet == null ? null : sinceStart - benchRet;
  const S = s.strategy;
  return (
    <>
      {s.halt && <div className="halt">⛔ {sel} halted: {s.halt}</div>}
      <section className="grid">
        <div className="card hero">
          <div className="label">{sel} · equity · {S.model}</div>
          <div className="big">{usd(s.equity)}</div>
          <div className="row">
            <Stat label="today" value={pct(day)} t={tone(day)} />
            <Stat label={`on ${usd(s.contributed)} contributed`} value={pct(sinceStart)} t={tone(sinceStart)} />
            <Stat label={s.benchmarkValue == null ? "vs benchmark" : `vs benchmark ${usd(s.benchmarkValue)}`} value={gap == null ? "day 1" : `${(gap * 100).toFixed(1)} pts`} t={tone(gap)} />
            <Stat label="cash" value={usd(s.cash)} />
          </div>
          <Curve points={s.curve} baseline={s.contributed} />
        </div>

        <div className="card">
          <div className="label">Cycle</div>
          <dl className="kv">
            <dt>last run</dt><dd>{s.lastRun ? new Date(s.lastRun).toLocaleString() : "never"}</dd>
            <dt>next run</dt><dd>{s.nextRun ?? "—"}</dd>
            <dt>last error</dt><dd className={s.lastError ? "down" : ""}>{s.lastError ?? "none"}</dd>
            <dt>decisions</dt><dd>09:45 · 12:30 · 15:30 ET · until {S.ends}</dd>
            <dt>guards</dt><dd>every 30 min · −{Math.round(S.stop_loss_pct * 100)}% stop · −{Math.round(S.trailing_stop_pct * 100)}% trailing · trim to {Math.round(S.max_position_pct * 100)}%</dd>
            <dt>caps</dt><dd>{Math.round(S.max_position_pct * 100)}% per symbol · {Math.round(S.max_sector_pct * 100)}% per sector · {S.max_orders} orders/cycle · {Math.round(S.daily_stop_pct * 100)}% daily stop</dd>
            <dt>model</dt><dd>{S.model} · reasoning {S.reasoning}</dd>
          </dl>
          <div className="label" style={{ marginTop: 16 }}>Watchlist · day change</div>
          <div className="heat">
            {Object.entries(s.watchlist).map(([sector, syms]) => (
              <div key={sector}>
                <div className="muted small">{sector}</div>
                <div className="chips">
                  {syms.map((sym) => (
                    <span key={sym} className={`chip ${tone(s.prices[sym]?.dayPc)} ${s.positions.some((p) => p.symbol === sym) ? "held" : ""}`} title={s.prices[sym]?.price ? usd(s.prices[sym].price) : ""}>
                      {sym} <b>{pct(s.prices[sym]?.dayPc, 1)}</b>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <div className="label">Positions</div>
          {s.positions.length === 0 ? <p className="muted">all cash</p> : (
            <div className="scroll"><table>
              <thead><tr><th>symbol</th><th>sector</th><th className="r">value</th><th className="r">price</th><th className="r">today</th><th className="r">unrealized</th></tr></thead>
              <tbody>
                {s.positions.map((p) => (
                  <tr key={p.symbol}>
                    <td><b>{p.symbol}</b></td><td className="muted">{p.sector}</td>
                    <td className="r">{usd(p.marketValue)}</td><td className="r">{usd(p.price)}</td>
                    <td className={`r ${tone(p.dayChangePc)}`}>{pct(p.dayChangePc)}</td>
                    <td className={`r ${tone(p.unrealizedPl)}`}>{usd(p.unrealizedPl)} · {pct(p.unrealizedPlpc, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </div>

        <div className="card log">
          <div className="label">Decision log</div>
          {s.journal.length === 0 && <p className="muted">no cycles yet</p>}
          {s.journal.map((e) => <JournalEntry key={e.ts} e={e} strategy={sel} />)}
        </div>
      </section>
    </>
  );
}

function JournalEntry({ e, strategy }: { e: Entry; strategy: string }) {
  const [saw, setSaw] = useState<any | null | "loading" | string>(null);
  const load = async () => {
    setSaw("loading");
    const r = await fetch(`/api/prompt?strategy=${strategy}&ts=${encodeURIComponent(e.ts)}`);
    setSaw(r.ok ? await r.json() : (await r.json()).error);
  };
  const isCycle = (e.kind ?? "cycle") === "cycle";
  return (
    <article className="entry">
      <div className="row">
        <b>{new Date(e.ts).toLocaleString()} {e.kind === "guard" && <span className="badge">guard</span>}</b>
        <span className="muted">
          {e.equity ? `equity ${usd(e.equity)}` : "run failed before reaching the account"}
          {e.cost_usd != null && ` · ${e.tokens_in?.toLocaleString()} in / ${e.tokens_out?.toLocaleString()} out · $${e.cost_usd.toFixed(4)}`}
          {isCycle && e.equity && !e.error && <> · <a href="#" onClick={(ev) => { ev.preventDefault(); saw && saw !== "loading" ? setSaw(null) : load(); }}>{saw && saw !== "loading" ? "hide" : "what it saw"}</a></>}
        </span>
      </div>
      {e.error && <div className="down">error: {e.error}</div>}
      <p>{e.market_view || <span className="muted">no view recorded</span>}</p>
      <ul>
        {e.placed.map((o, i) => <li key={`p${i}`}><span className={o.side === "buy" ? "up" : "down"}>{o.side}</span> {usd(o.notional_usd)} <b>{o.symbol}</b> <span className="muted">— {o.reason}</span></li>)}
        {e.rejected.map((o, i) => <li key={`r${i}`} className="muted">✗ {o.side} {usd(o.notional_usd)} {o.symbol} — {o.why}</li>)}
        {!e.placed.length && !e.rejected.length && <li className="muted">held</li>}
      </ul>
      {saw === "loading" && <p className="muted small">loading…</p>}
      {typeof saw === "string" && saw !== "loading" && <p className="muted small">{saw}</p>}
      {saw && typeof saw === "object" && <Saw p={saw} />}
    </article>
  );
}

/** The model's input for one cycle: features table, positions, news, and the instructions. */
function Saw({ p }: { p: any }) {
  const m: Record<string, any> = p.input?.market ?? {};
  const cols: [string, string][] = [["last", "last"], ["1d", "chg_1d_pct"], ["5d", "chg_5d_pct"], ["20d", "chg_20d_pct"], ["vs 20d hi", "from_20d_high_pct"], ["vs 20d lo", "from_20d_low_pct"], ["ma20", "above_ma20"], ["ma50", "above_ma50"], ["vol20", "daily_vol_20d_pct"], ["vol×", "volume_vs_20d_avg"]];
  const cell = (v: any) => v == null ? "–" : typeof v === "boolean" ? (v ? "▲" : "▼") : typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v);
  return (
    <div className="saw">
      <div className="muted small">{p.model} · reasoning {p.reasoning} · {p.input?.market_hours} · cash {usd(p.input?.cash ?? 0)} · buys {p.input?.buys_blocked ? "blocked" : "allowed"}</div>
      <div className="scroll">
        <table className="small">
          <thead><tr><th>symbol</th>{cols.map(([h]) => <th key={h} className="r">{h}</th>)}</tr></thead>
          <tbody>{Object.entries(m).map(([sym, f]) => (
            <tr key={sym}><td><b>{sym}</b> <span className="muted">{f.sector}</span></td>
              {cols.map(([, k]) => <td key={k} className={`r ${k.startsWith("chg") || k.startsWith("from") ? tone(f[k] ?? null) : k.startsWith("above") ? (f[k] ? "up" : f[k] === false ? "down" : "") : ""}`}>{cell(f[k])}{k.startsWith("chg") || k.startsWith("from") || k === "daily_vol_20d_pct" ? (f[k] == null ? "" : "%") : ""}</td>)}
            </tr>
          ))}</tbody>
        </table>
      </div>
      {!!p.input?.positions?.length && <div className="small"><span className="muted">positions:</span> {p.input.positions.map((x: any) => `${x.symbol} $${(+x.market_value).toFixed(0)} (pl ${(+x.unrealized_pl).toFixed(2)})`).join(", ")}</div>}
      <details><summary className="muted small">news in prompt ({p.input?.news_24h?.length ?? 0})</summary><ul className="small">{(p.input?.news_24h ?? []).map((n: string, i: number) => <li key={i}>{n}</li>)}</ul></details>
      <details><summary className="muted small">instructions</summary><pre className="small">{p.instructions}</pre></details>
    </div>
  );
}

type RunResult = { job: string; at: string; strategy?: string; entry?: Entry; text?: string };

/** main.py prints a pretty JSON journal entry (indent=1, so it ends in "\n}") possibly followed by other text. */
function parseRun(job: string, out: string): RunResult {
  const start = out.indexOf("{"), end = out.indexOf("\n}", start);
  if (start >= 0 && end > start) {
    try {
      const entry = JSON.parse(out.slice(start, end + 2));
      const rest = out.slice(end + 2).trim();
      return { job, at: entry.ts, entry, text: rest || undefined };
    } catch { /* fall through */ }
  }
  return { job, at: new Date().toISOString(), text: out.trim() || "no output" };
}

function RunOutput({ r, onClose }: { r: RunResult; onClose: () => void }) {
  const e = r.entry;
  const status = !e ? null : e.error ? "error" : e.placed.length ? "traded" : "held";
  return (
    <div className={`runout ${status ?? ""}`}>
      <div className="row">
        <span><b>{r.strategy ? `${r.strategy} · ` : ""}{r.job === "guard" ? "Guard" : "Decision"} run</b> <span className="muted">· {new Date(r.at).toLocaleTimeString()}</span></span>
        <span className="row" style={{ gap: 12 }}>
          {e && <span className="muted">placed {e.placed.length} · rejected {e.rejected.length} · equity {usd(e.equity)}</span>}
          <button onClick={onClose} aria-label="dismiss">×</button>
        </span>
      </div>
      {e?.error && <div className="down">error: {e.error}</div>}
      {e?.market_view && <p>{e.market_view}</p>}
      {e && (e.placed.length + e.rejected.length > 0) && (
        <ul>
          {e.placed.map((o, i) => <li key={`p${i}`}><span className={o.side === "buy" ? "up" : "down"}>{o.side}</span> {usd(o.notional_usd)} <b>{o.symbol}</b> <span className="muted">— {o.reason}</span></li>)}
          {e.rejected.map((o, i) => <li key={`r${i}`} className="muted">✗ {o.side} {usd(o.notional_usd)} {o.symbol} — {o.why}</li>)}
        </ul>
      )}
      {e && status === "held" && <p className="muted">held, no orders</p>}
      {r.text && (e ? <details><summary className="muted small">additional output</summary><pre>{r.text}</pre></details> : <p>{r.text}</p>)}
    </div>
  );
}

function Stat({ label, value, t = "" }: { label: string; value: string; t?: string }) {
  return <div className="stat"><div className="muted small">{label}</div><div className={t}>{value}</div></div>;
}

/** Single-series equity line with crosshair + tooltip. */
function Curve({ points, baseline }: { points: [number, number][]; baseline: number }) {
  const [i, setI] = useState<number | null>(null);
  const W = 640, H = 140, P = 6;
  if (points.length < 2) return <p className="muted small">equity curve appears after the first market day</p>;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys, baseline), Math.max(...ys, baseline)];
  const X = (t: number) => P + ((t - x0) / (x1 - x0 || 1)) * (W - 2 * P);
  const Y = (v: number) => H - P - ((v - y0) / (y1 - y0 || 1)) * (H - 2 * P);
  const d = points.map(([t, v], k) => `${k ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const t = x0 + ((e.clientX - r.left) / r.width) * (x1 - x0);
    let best = 0;
    for (let k = 1; k < xs.length; k++) if (Math.abs(xs[k] - t) < Math.abs(xs[best] - t)) best = k;
    setI(best);
  };
  const hover = i == null ? null : points[i];
  return (
    <div className="curve">
      <svg viewBox={`0 0 ${W} ${H}`} onMouseMove={onMove} onMouseLeave={() => setI(null)} role="img" aria-label="Equity over time">
        <line x1={P} x2={W - P} y1={Y(baseline)} y2={Y(baseline)} className="baseline" />
        <path d={d} className="series" />
        {hover && <>
          <line x1={X(hover[0])} x2={X(hover[0])} y1={P} y2={H - P} className="crosshair" />
          <circle cx={X(hover[0])} cy={Y(hover[1])} r={4} className="marker" />
        </>}
      </svg>
      <div className="muted small">{hover ? `${new Date(hover[0]).toLocaleString()} · ${usd(hover[1])}` : `${new Date(x0).toLocaleDateString()} → now · dashed line = ${usd(baseline)} contributed`}</div>
    </div>
  );
}
