"""Telegram command bot. Long-polls getUpdates and answers only the configured chat.

  /status            equity, day change, return, positions, state per strategy
  /positions [name]  open positions
  /log [name]        last decision per strategy
  /halt <name>       stop a strategy (writes runs/<name>/HALT)
  /resume <name>     clear the halt
  /run <name>        start a decision cycle now (no summary)
  /help

Run by com.ies.trading-agent-bot.plist (KeepAlive). One instance at a time via runs/.bot.lock.
"""

import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from main import ET, ROOT, RUNS, STRATEGIES, esc, notify, read_journal, read_ledger_value, trading

TOKEN, CHAT = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
COMMANDS = {
    "status": "equity, day change, return and state per strategy",
    "positions": "open positions, optionally for one strategy",
    "log": "last decision per strategy",
    "halt": "halt a strategy: /halt main",
    "resume": "resume a halted strategy",
    "run": "start a decision cycle now: /run crypto",
    "help": "this list",
}


def api(method: str, **params):
    body = json.dumps(params).encode()
    req = urllib.request.Request(f"{API}/{method}", body, {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=40))["result"]


def configured():
    return [S for S in STRATEGIES.values() if S.keys]


def pick(arg: str | None):
    if not arg:
        return configured(), None
    S = STRATEGIES.get(arg.lower())
    if not S or not S.keys:
        return [], f"unknown or unconfigured strategy <b>{esc(arg)}</b>. Configured: {', '.join(s.name for s in configured())}"
    return [S], None


def status(_):
    rows = []  # (score, line) ; score = gap to own benchmark, or raw return before a ledger exists
    for S in configured():
        try:
            a = trading(S).get_account()
            eq, last = float(a.equity), float(a.last_equity)
            contributed, bench = read_ledger_value(S)
            ret = eq / contributed - 1
            n = len(trading(S).get_all_positions())
            state = "HALTED" if (S.dir / "HALT").exists() else "live"
            score = ret - (bench / contributed - 1) if bench else ret
            gap = f" · vs bench {score * 100:+.1f} pts" if bench else ""
            rows.append((score, f"<b>{S.name}</b> {state} · ${eq:.2f} · {eq / last - 1:+.2%} today · {ret:+.2%} on ${contributed:.0f}{gap} · {n} pos"))
        except Exception as e:
            rows.append((None, f"<b>{S.name}</b> error: {esc(repr(e)[:120])}"))
    rows.sort(key=lambda r: -(r[0] if r[0] is not None else float("-inf")))
    if sum(r[0] is not None for r in rows) >= 2:
        rows[0] = (rows[0][0], "🏆 " + rows[0][1])
    return "\n".join(line for _, line in rows) or "no configured strategies"


def positions(arg):
    chosen, err = pick(arg)
    if err:
        return err
    out = []
    for S in chosen:
        ps = trading(S).get_all_positions()
        out.append(f"<b>{S.name}</b> " + (", ".join(f"{S.canon(p.symbol)} ${float(p.market_value):.0f} ({float(p.unrealized_plpc):+.1%})" for p in ps) or "all cash"))
    return "\n".join(out)


def log(arg):
    chosen, err = pick(arg)
    if err:
        return err
    out = []
    for S in chosen:
        j = read_journal(S)
        if not j:
            out.append(f"<b>{S.name}</b> no runs yet")
            continue
        e = j[-1]
        when = datetime.fromisoformat(e["ts"]).astimezone(ET).strftime("%m/%d %H:%M ET")
        acts = ", ".join(f"{o['side']} ${o['notional_usd']:.0f} {o['symbol']}" for o in e["placed"]) or "held"
        out.append(f"<b>{S.name}</b> {when} {e.get('kind', 'cycle')} · {acts}" + (f"\n<i>{esc(e['market_view'][:300])}</i>" if e.get("market_view") else "") + (f"\n⚠️ {esc(str(e['error'])[:200])}" if e.get("error") else ""))
    return "\n\n".join(out)


def halt(arg):
    if not arg:
        return "usage: /halt &lt;strategy&gt;"
    chosen, err = pick(arg)
    if err:
        return err
    (chosen[0].dir / "HALT").write_text(f"manual halt from telegram {datetime.now().isoformat()}")
    return f"⛔ <b>{chosen[0].name}</b> halted. /resume {chosen[0].name} to continue."


def resume(arg):
    if not arg:
        return "usage: /resume &lt;strategy&gt;"
    chosen, err = pick(arg)
    if err:
        return err
    p = chosen[0].dir / "HALT"
    if not p.exists():
        return f"<b>{chosen[0].name}</b> is not halted"
    p.unlink()
    return f"▶ <b>{chosen[0].name}</b> resumed"


def run(arg):
    if not arg:
        return "usage: /run &lt;strategy&gt;"
    chosen, err = pick(arg)
    if err:
        return err
    S = chosen[0]
    subprocess.Popen([os.path.expanduser("~/.local/bin/uv"), "run", "--frozen", "main.py", "--strategy", S.name, "--no-summary"],
                     cwd=ROOT, stdout=open(ROOT / "agent.log", "a"), stderr=subprocess.STDOUT)
    return f"started a decision cycle for <b>{S.name}</b>; the result arrives here when it finishes"


def help_(_):
    return "\n".join(f"/{c} — {d}" for c, d in COMMANDS.items())


HANDLERS = {"status": status, "positions": positions, "log": log, "halt": halt, "resume": resume, "run": run, "help": help_, "start": help_}


def handle(text: str) -> str:
    cmd, _, arg = text.strip().partition(" ")
    cmd = cmd.lstrip("/").split("@")[0].lower()
    fn = HANDLERS.get(cmd)
    return fn(arg.strip() or None) if fn else f"unknown command /{esc(cmd)}\n\n{help_(None)}"


def main() -> None:
    with (RUNS / ".bot.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("bot already running")
        api("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS.items()])
        print("bot up; polling")
        offset = None
        while True:
            try:
                for u in api("getUpdates", offset=offset, timeout=30, allowed_updates=["message"]):
                    offset = u["update_id"] + 1
                    m = u.get("message") or {}
                    if str(m.get("chat", {}).get("id")) != CHAT or not m.get("text", "").startswith("/"):
                        continue  # only our chat, only commands
                    try:
                        reply = handle(m["text"])
                    except Exception as e:
                        reply = f"⚠️ command failed: <code>{esc(repr(e)[:300])}</code>"
                    notify(reply)
            except Exception as e:  # network blip: back off and keep polling
                print("poll error:", repr(e)[:200])
                time.sleep(5)


if __name__ == "__main__":
    main()
