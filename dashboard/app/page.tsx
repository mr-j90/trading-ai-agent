"use client";
import { useEffect, useState } from "react";

type Position = { symbol: string; sector: string; qty: number; marketValue: number; price: number; unrealizedPl: number; unrealizedPlpc: number; dayChangePc: number };
type Entry = { ts: string; equity: number; market_view: string; placed: any[]; rejected: any[]; error: string | null };
type State = {
  now: string; equity: number; cash: number; startOfDayEquity: number; benchmarkReturn: number | null; halt: string | null;
  lastRun: string | null; lastError: string | null; nextRun: string | null; curve: [number, number][];
  positions: Position[]; watchlist: Record<string, string[]>; prices: Record<string, { price: number; dayPc: number | null }>; journal: Entry[];
};

const START = 500;
const POLL_MS = 15_000;
const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const pct = (n: number | null, d = 2) => (n == null ? "–" : `${n >= 0 ? "+" : ""}${(n * 100).toFixed(d)}%`);
const tone = (n: number | null) => (n == null ? "" : n >= 0 ? "up" : "down");
const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });

export default function Page() {
  const [s, setS] = useState<State | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () =>
      fetch("/api/state").then(async (r) => {
        if (!alive) return;
        if (!r.ok) throw new Error(await r.text());
        setS(await r.json()); setErr(null);
      }).catch((e) => alive && setErr(String(e)));
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (!s) return <main className="wrap"><p className="muted">{err ?? "loading…"}</p></main>;
  const sinceStart = s.equity / START - 1;
  const day = s.equity / s.startOfDayEquity - 1;
  const gap = s.benchmarkReturn == null ? null : sinceStart - s.benchmarkReturn;

  return (
    <main className="wrap">
      <header className="bar">
        <span><span className={`dot ${s.halt ? "halted" : "live"}`} /> trading agent · paper · {s.halt ? "HALTED" : "live"}</span>
        <span className="muted">updated {hhmm(s.now)}{err ? ` · refresh failed: ${err}` : ""}</span>
      </header>
      {s.halt && <div className="halt">⛔ halted: {s.halt}</div>}

      <section className="grid">
        <div className="card hero">
          <div className="label">Equity</div>
          <div className="big">{usd(s.equity)}</div>
          <div className="row">
            <Stat label="today" value={pct(day)} t={tone(day)} />
            <Stat label="since $500" value={pct(sinceStart)} t={tone(sinceStart)} />
            <Stat label="vs benchmark" value={gap == null ? "day 1" : `${(gap * 100).toFixed(1)} pts`} t={tone(gap)} />
            <Stat label="cash" value={usd(s.cash)} />
          </div>
          <Curve points={s.curve} />
        </div>

        <div className="card">
          <div className="label">Cycle</div>
          <dl className="kv">
            <dt>last run</dt><dd>{s.lastRun ? new Date(s.lastRun).toLocaleString() : "never"}</dd>
            <dt>next run</dt><dd>{s.nextRun ?? "—"}</dd>
            <dt>last error</dt><dd className={s.lastError ? "down" : ""}>{s.lastError ?? "none"}</dd>
            <dt>decisions</dt><dd>09:45 · 12:30 · 15:30 ET</dd>
            <dt>guards</dt><dd>every 30 min · −8% stop · −10% trailing · trim to 20%</dd>
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
            <table>
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
            </table>
          )}
        </div>

        <div className="card log">
          <div className="label">Decision log</div>
          {s.journal.length === 0 && <p className="muted">no cycles yet</p>}
          {s.journal.map((e) => (
            <article key={e.ts} className="entry">
              <div className="row"><b>{new Date(e.ts).toLocaleString()}</b><span className="muted">equity {usd(e.equity)}</span></div>
              {e.error && <div className="down">error: {e.error}</div>}
              <p>{e.market_view || <span className="muted">no view recorded</span>}</p>
              <ul>
                {e.placed.map((o, i) => <li key={`p${i}`}><span className={o.side === "buy" ? "up" : "down"}>{o.side}</span> {usd(o.notional_usd)} <b>{o.symbol}</b> <span className="muted">— {o.reason}</span></li>)}
                {e.rejected.map((o, i) => <li key={`r${i}`} className="muted">✗ {o.side} {usd(o.notional_usd)} {o.symbol} — {o.why}</li>)}
                {!e.placed.length && !e.rejected.length && <li className="muted">held</li>}
              </ul>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}

function Stat({ label, value, t = "" }: { label: string; value: string; t?: string }) {
  return <div className="stat"><div className="muted small">{label}</div><div className={t}>{value}</div></div>;
}

/** Single-series equity line with crosshair + tooltip. */
function Curve({ points }: { points: [number, number][] }) {
  const [i, setI] = useState<number | null>(null);
  const W = 640, H = 140, P = 6;
  if (points.length < 2) return <p className="muted small">equity curve appears after the first market day</p>;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys, START), Math.max(...ys, START)];
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
        <line x1={P} x2={W - P} y1={Y(START)} y2={Y(START)} className="baseline" />
        <path d={d} className="series" />
        {hover && <>
          <line x1={X(hover[0])} x2={X(hover[0])} y1={P} y2={H - P} className="crosshair" />
          <circle cx={X(hover[0])} cy={Y(hover[1])} r={4} className="marker" />
        </>}
      </svg>
      <div className="muted small">{hover ? `${new Date(hover[0]).toLocaleString()} · ${usd(hover[1])}` : `${new Date(x0).toLocaleDateString()} → now · dashed line = $${START}`}</div>
    </div>
  );
}
