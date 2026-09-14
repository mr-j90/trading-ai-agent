UID := $(shell id -u)
JOBS := com.ies.trading-agent com.ies.trading-agent-retry com.ies.trading-agent-guard
PORT ?= 3210

.PHONY: help up down dashboard test dry-run guard run status logs install uninstall

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

up: install  ## everything on: load the 3 launchd jobs + dashboard in the background
	@if lsof -ti tcp:$(PORT) >/dev/null; then echo "dashboard already running on http://localhost:$(PORT)"; \
	else (cd dashboard && nohup npm run dev -- --port $(PORT) > ../dashboard.log 2>&1 &); echo "dashboard starting on http://localhost:$(PORT) (log: dashboard.log)"; fi

down: uninstall  ## everything off: unload the jobs + stop the dashboard
	@pids=$$(lsof -ti tcp:$(PORT)); if [ -n "$$pids" ]; then for p in $$pids; do kill $$(ps -o ppid= -p $$p) $$p 2>/dev/null; done; echo "dashboard stopped"; else echo "dashboard not running"; fi

dashboard:  ## run the dashboard in the foreground on $(PORT)
	cd dashboard && npm run dev -- --port $(PORT)

test:  ## run the self-checks and type-check the dashboard
	uv run python test_main.py
	cd dashboard && npx tsc --noEmit -p .

S ?= main  # strategy for dry-run / guard / run, e.g. `make dry-run S=luna`; `S=--all` for every strategy
STRAT = $(if $(filter --all,$(S)),--all,--strategy $(S))

dry-run:  ## one decision cycle for $(S), nothing submitted
	uv run --frozen main.py $(STRAT) --dry-run

guard:  ## one guard pass for $(S), nothing submitted
	uv run --frozen main.py $(STRAT) --guard --dry-run

run:  ## one REAL decision cycle for $(S) now (places paper orders, no summary)
	uv run --frozen main.py $(STRAT) --no-summary

status:  ## launchd job states and each strategy's last journal entry
	@for j in $(JOBS); do printf "%-32s" $$j; launchctl print gui/$(UID)/$$j 2>/dev/null | awk '/^\t(state|runs|last exit code) =/{printf "%s ", $$0}'; echo; done
	@for d in runs/*/; do n=$$(basename $$d); [ -f $$d/HALT ] && h=" HALTED" || h=""; printf "%-10s%s " $$n "$$h"; \
	  if [ -s $$d/journal.jsonl ]; then tail -n 1 $$d/journal.jsonl | python3 -c 'import sys,json; [print(e["ts"][:16], e.get("kind","cycle"), "placed", len(e["placed"]), "err" if e["error"] else "", (e["market_view"] or "")[:60]) for e in map(json.loads, sys.stdin)]'; else echo "no runs"; fi; done

logs:  ## follow agent.log
	tail -f agent.log

install:  ## copy plists to ~/Library/LaunchAgents and (re)load all three jobs
	@for j in $(JOBS); do cp $$j.plist ~/Library/LaunchAgents/; launchctl bootout gui/$(UID)/$$j 2>/dev/null || true; launchctl bootstrap gui/$(UID) ~/Library/LaunchAgents/$$j.plist && echo "loaded $$j"; done

uninstall:  ## unload all three jobs (files stay)
	@for j in $(JOBS); do launchctl bootout gui/$(UID)/$$j 2>/dev/null && echo "unloaded $$j" || true; done
