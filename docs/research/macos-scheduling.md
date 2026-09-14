# Scheduling `uv run main.py` on macOS during US market hours

Resolves GitHub issue #5. Researched 2026-09-14 on macOS Darwin 25.5.0, uv 0.10.9, alpaca-py 0.44.0.

## TL;DR

Use a per-user **launchd LaunchAgent** with an array of `StartCalendarInterval` dictionaries (Weekday 1–5, a few fixed times), call **`uv` by absolute path** with `PATH` and `WorkingDirectory` set in the plist, and make **`main.py` gate itself on `TradingClient.get_clock().is_open`** so holidays, early closes, and late wake-ups are skipped by the API rather than by the schedule. Keep the Mac awake with `sudo pmset repeat wakeorpoweron MTWRF <time>` plus `caffeinate -i` around the run. Don't use cron.

## 1. launchd vs cron

| | launchd `StartCalendarInterval` | cron |
|---|---|---|
| Apple's position | "The preferred way to add a timed job is to use launchd." [1] | "Although it is still supported, cron is not a recommended solution. It has been deprecated in favor of launchd." [1] |
| Mac asleep at fire time | "launchd will start the job the next time the computer wakes up. If multiple intervals transpire before the computer is woken, those events will be coalesced into one event upon wake from sleep." [2] | "If the system is turned off or asleep, cron jobs do not execute; they will not run until the next designated time occurs." [1] |
| Logging | `StandardOutPath` / `StandardErrorPath` keys create the file for you [2] | stdout mailed via `MAILTO`, or redirect manually [4] |
| Manage | `launchctl bootstrap/bootout gui/<uid>` [3] | `crontab -e` |

Note that `StartInterval` (every N seconds) does **not** get the wake-catch-up behaviour: "If the system is asleep during the time of the next scheduled interval firing, that interval will be missed due to shortcomings in kqueue(3)." [2] Use `StartCalendarInterval`, not `StartInterval`.

## 2. Sleep / lid-closed behaviour

- **Missed `StartCalendarInterval` fires on wake, coalesced to one run.** [1][2] So a Mac asleep 09:00–14:00 ET runs the job once at 14:00, not three times. That single run may land outside market hours (or after an early close) — which is why the script itself must check the Alpaca clock (section 4).
- **Nothing runs while asleep.** launchd is a userland process; a sleeping Mac executes nothing. Lid closed on a MacBook = asleep unless it is in closed-display mode with external display and power (Apple's clamshell requirements were not reachable on a primary page during this research; treat "lid closed" as "asleep" for planning).
- `pmset -g` on this machine shows `sleep 0` (never), `lidwake 1`, `powernap 1`, `womp 1` — but `sleep` is currently held at 0 only by assertions from other apps ("sleep prevented by powerd, Amphetamine, Claude, ..., caffeinate") [5], so don't rely on it.

## 3. Keeping the Mac awake

Two primary tools, both in-box:

1. **`pmset repeat`** — scheduled wake/power-on. "`repeat` is for setting up daily/weekly power on and power off events. Note that you may only have one pair of repeating events scheduled — a 'power on' event and a 'power off' event." `weekdays - a subset of MTWRFSU`. [6] Apple's current user guide says scheduling is done via `pmset` in Terminal (no System Settings UI): "sudo pmset repeat wake M 8:00:00: Schedule your Mac to wake at 8:00 a.m. every Monday." [7]
   ```sh
   # wake 5 min before the first cycle, Mon–Fri (local time, see section 5)
   sudo pmset repeat wakeorpoweron MTWRF 08:25:00
   pmset -g sched   # verify
   ```
   Requires root (`pmset must be run as root in order to modify any settings` [6]). Only one repeating pair exists system-wide, so this wakes for the day; it does not re-wake for each cycle. Combine with (2) or with `sudo pmset -c sleep 0` (never idle-sleep on AC).
2. **`caffeinate`** — assertion for the duration of the job. "If a utility is specified, caffeinate creates the assertions on the utility's behalf, and those assertions will persist for the duration of the utility's execution." `-i` prevents idle sleep; `-s` prevents system sleep but "is valid only when system is running on AC power." [8] Wrap the launchd command: `caffeinate -is /Users/jordan/.local/bin/uv run ...`. This keeps a run from being suspended mid-cycle; it does not wake a sleeping Mac.

## 4. Alpaca clock / calendar API (skip holidays and early closes)

alpaca-py 0.44 `TradingClient` (`.venv/lib/python3.14/site-packages/alpaca/trading/client.py`):

- `get_clock() -> Clock` — `GET /v2/clock`. Model `Clock(timestamp, is_open, next_open, next_close)`; docstring: "The market clock for US equity markets. Timestamps are in eastern time." [9] API: "The clock API serves the current market timestamp, whether or not the market is currently open, as well as the times of the next market open and close." Example payload has tz-aware ISO timestamps (`"next_close": "2025-06-24T16:00:00-04:00"`). [10]
- `get_calendar(GetCalendarRequest(start, end)) -> list[Calendar]` — `GET /v2/calendar`. "The calendar API serves the full list of market days from 1970 to 2029 ... the response also contains the specific open and close times for the market days, taking into account early closures." Fields: `date`, `open` ("HH:MM"), `close`, `session_open`/`session_close` ("HHMM", extended session), `settlement_date`. [11] alpaca-py parses `open`/`close` into **naive** `datetime`s (`datetime.strptime(date + " " + open, "%Y-%m-%d %H:%M")`) — no tzinfo, ET implied. [9]
- Both endpoints are rate limited (headers `X-RateLimit-*`, 429 on excess); alpaca-py retries 429/504 up to 3 times with a 3 s wait by default (`alpaca/common/constants.py`). [10][12]

**Recommendation: gate on `get_clock()` at the top of every run.** One call, tz-aware, already accounts for holidays and early closes (`is_open` is false after a 13:00 early close). `get_calendar` is only needed if you want to *plan* (e.g. pick the last cycle time relative to `close`) — not to skip.

```python
# main.py — ponytail: one API call replaces any local holiday table
clock = client.get_clock()
if not clock.is_open:
    print(f"market closed; next open {clock.next_open:%Y-%m-%d %H:%M %Z}")
    raise SystemExit(0)
```

Exit 0 (not non-zero) so launchd's last-exit-status stays clean and you can tell "skipped" from "crashed" in the log.

## 5. Sample LaunchAgent

Schedule in **local** time — `StartCalendarInterval` has no timezone key ("Minute (0-59)... Hour (0-23)... Weekday" only) [2]. This Mac is on US Central (`date +%Z` → CDT); Central and Eastern change DST on the same dates, so ET = CT + 1 h year-round and the mapping below is stable. If the Mac ever runs in a non-US zone, re-derive the hours (or schedule generously and let the clock gate decide).

Market 09:30–16:00 ET = 08:30–15:00 CT. Three cycles: 09:45 ET, 12:30 ET, 15:30 ET.

`~/Library/LaunchAgents/com.ies.trading-agent.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.ies.trading-agent</string>

  <!-- absolute path: launchd PATH is /usr/bin:/bin:/usr/sbin:/sbin, uv is not on it -->
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-is</string>
    <string>/Users/jordan/.local/bin/uv</string>
    <string>run</string>
    <string>--frozen</string>
    <string>main.py</string>
  </array>

  <!-- load_dotenv() reads ./.env, and uv discovers pyproject.toml from cwd -->
  <key>WorkingDirectory</key>
  <string>/Users/jordan/Developer/trading-ai-agent</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/Users/jordan/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>

  <!-- Mon–Fri, local (Central) time: 08:45, 11:30, 14:30 = 09:45, 12:30, 15:30 ET -->
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>30</integer></dict>
  </array>

  <key>StandardOutPath</key>
  <string>/Users/jordan/Library/Logs/trading-agent.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/jordan/Library/Logs/trading-agent.log</string>

  <!-- default ProcessType throttles CPU/IO; a trading run is user-facing work -->
  <key>ProcessType</key>
  <string>Interactive</string>
</dict>
</plist>
```

Install / test / remove (modern subcommands; `load`/`unload` are legacy [3]):

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ies.trading-agent.plist
launchctl kickstart -k gui/$(id -u)/com.ies.trading-agent   # run now, ignore schedule
launchctl print gui/$(id -u)/com.ies.trading-agent | grep -E 'state|last exit'
launchctl bootout gui/$(id -u)/com.ies.trading-agent
```

`Weekday` is an integer, "Missing arguments are considered to be wildcard" [2]; no weekday range syntax, hence one dict per weekday × time (15 entries for 3 cycles). Wildcarding Weekday (7 dicts fewer) is also fine — the clock gate exits on weekends; the cost is two no-op API calls per weekend day.

## 6. Pitfalls of launchd running uv (verified empirically on this Mac)

A throwaway agent that ran `env; pwd; which uv` under `launchctl bootstrap gui/501` produced [13]:

```
PATH=/usr/bin:/bin:/usr/sbin:/sbin
PWD=/
HOME=/Users/jordan  SHELL=/bin/zsh  USER=jordan  TMPDIR=/var/folders/.../T/
uv NOT on PATH
```

- **PATH.** Your `~/.zshrc` is never sourced. launchd resolves a relative `ProgramArguments[0]` against `_PATH_STDPATH` [2] = `/usr/bin:/bin:/usr/sbin:/sbin` (`paths.h` [14]). `uv` lives in `/Users/jordan/.local/bin` → "uv NOT on PATH". Fix: absolute path in `ProgramArguments` *and* export `PATH` via `EnvironmentVariables` (uv may shell out to `git` or a Homebrew tool during a sync). `launchctl getenv PATH` is empty on this machine, so nothing global rescues you.
- **Working directory is `/`.** `load_dotenv()` in `main.py` looks for `./.env`; `uv run` discovers `pyproject.toml`/`.venv` from cwd. Set `WorkingDirectory` (or `uv run --directory ...`). Never bake credentials into the plist's `EnvironmentVariables`; leave them in the git-ignored `.env`.
- **`uv run` syncs first.** "When using `run`, uv will ensure that the project environment is up-to-date before running the given command." [15] Pass `--frozen` ("Instead of checking if the lockfile is up-to-date, uses the versions in the lockfile as the source of truth") so a scheduled run never re-resolves; `--no-sync` if the venv must not be touched at all (`UV_FROZEN=1` / `UV_NO_SYNC=1` env equivalents). [16]
- **Logging.** `StandardOutPath`/`StandardErrorPath` create the file with your umask and *append*; no rotation. Python block-buffers stdout when not a tty, so a crash can lose the tail — set `PYTHONUNBUFFERED=1` (done above). Do not redirect to `/dev/null`; launchd asks you to use these keys instead [2][17]. `~/Library/Logs` is readable by Console.app.
- **Resource throttling.** "If left unspecified, the system will apply light resource limits to the job, throttling its CPU usage and I/O bandwidth." [2] Set `ProcessType` `Interactive` (or `Standard`) if the run is slow.
- **`RunAtLoad` off.** Leave unset; Apple: "This key should be avoided, as speculative job launches have an adverse effect on system-boot and user-login scenarios." [2] Use `kickstart` for manual runs.
- **Overlapping runs.** A calendar fire while the job is still running is dropped, not queued (launchd runs one instance per label). Keep a cycle well under the gap between cycles.
- **ThrottleInterval** defaults to 10 s between spawns [2]; irrelevant at 3 runs/day but explains why rapid `kickstart -k` tests can be delayed.

## 7. Failure modes and what happens

| Scenario | Result |
|---|---|
| Market holiday, Mac awake | 3 runs fire; each exits 0 at the clock gate |
| Early close (13:00 ET) | 09:45 and 12:30 runs trade; 15:30 run exits at the gate |
| Mac asleep 08:00–13:00 CT | 08:45 + 11:30 fires coalesce into one run at wake (~13:00 CT, market open → trades); 14:30 runs normally |
| Mac asleep all day, wakes 18:00 | one coalesced run, gate says closed, exit 0 |
| Lid closed, on battery | asleep → see above; `pmset repeat wake` may not wake a closed notebook — keep it open/on AC |
| `uv` missing from PATH | log shows `env: uv: No such file` — fixed by absolute path |

## Sources

1. Apple, *Daemons and Services Programming Guide — Scheduling Timed Jobs*: https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html
2. `man 5 launchd.plist` (macOS 25.5.0), sections `Program`, `ProgramArguments`, `RunAtLoad`, `WorkingDirectory`, `EnvironmentVariables`, `StartInterval`, `StartCalendarInterval`, `StandardOutPath`, `StandardErrorPath`, `ThrottleInterval`, `ProcessType`
3. `man 1 launchctl` (macOS 25.5.0), `bootstrap | bootout`, `kickstart`, `print`, `LEGACY SUBCOMMANDS` ("Recommended alternative subcommands: bootstrap | bootout | enable | disable")
4. `man 5 crontab` (macOS): `MAILTO`, `SHELL=/bin/sh` defaults
5. `pmset -g` and `pmset -g sched` output on this machine, 2026-09-14
6. `man 1 pmset`, `SCHEDULED EVENT ARGUMENTS`, `SETTINGS` (`lidwake`, `sleep`, `womp`, `powernap`)
7. Apple, *Mac User Guide — Schedule your Mac to turn on or off*: https://support.apple.com/guide/mac-help/schedule-your-mac-to-turn-on-or-off-mchl40376151/mac
8. `man 8 caffeinate`
9. alpaca-py 0.44.0 source: `alpaca/trading/client.py` (`get_clock`, `get_calendar`), `alpaca/trading/models.py` (`Clock`, `Calendar.__init__`), `alpaca/trading/requests.py` (`GetCalendarRequest`)
10. Alpaca, *Get US Market Clock* (`GET /v2/clock`): https://docs.alpaca.markets/reference/legacyclock (markdown: https://docs.alpaca.markets/us/reference/legacyclock.md)
11. Alpaca, *Get US Market Calendar* (`GET /v2/calendar`): https://docs.alpaca.markets/reference/legacycalendar (markdown: https://docs.alpaca.markets/us/reference/legacycalendar.md)
12. alpaca-py 0.44.0 `alpaca/common/constants.py`: `DEFAULT_RETRY_ATTEMPTS = 3`, `DEFAULT_RETRY_WAIT_SECONDS = 3`, `DEFAULT_RETRY_EXCEPTION_CODES = [429, 504]`
13. Empirical: temporary LaunchAgent `com.ies.envprobe` bootstrapped into `gui/501`, kickstarted, output captured, booted out (2026-09-14)
14. `$(xcrun --show-sdk-path)/usr/include/paths.h`: `#define _PATH_STDPATH "/usr/bin:/bin:/usr/sbin:/sbin"`
15. uv docs, *Running commands in projects*: https://docs.astral.sh/uv/concepts/projects/run/
16. uv docs, *CLI reference — uv run* (`--frozen`, `--locked`, `--no-sync`, `--directory`, `--project`): https://docs.astral.sh/uv/reference/cli/ ; also `uv run --help` (uv 0.10.9)
17. Apple, *Daemons and Services Programming Guide — Creating Launch Daemons and Agents*: https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html

Not verified against a primary source: Apple's closed-display (clamshell) mode requirements; the relevant support page could not be located during this research, so lid-closed behaviour above is stated conservatively.
