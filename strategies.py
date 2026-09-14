"""Strategy definitions. Source of truth is strategies.json (shared with the dashboard)."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from watchlist import WATCHLIST

ROOT = Path(__file__).parent
RUNS = ROOT / "runs"


@dataclass(frozen=True)
class Strategy:
    name: str
    key_env: str  # env prefix: <key_env>_API_KEY / <key_env>_SECRET_KEY for that paper account
    description: str = ""
    asset_class: str = "us_equity"  # or "crypto": 24/7, GTC orders, crypto data endpoints
    watchlist: dict[str, list[str]] = field(default_factory=lambda: WATCHLIST)  # sector -> symbols
    model: str = "gpt-5.6-terra"
    reasoning: str = "low"
    style: str = ""  # extra prompt guidance appended to the shared instructions
    start_equity: float = 500.0
    max_position_pct: float = 0.20
    max_sector_pct: float = 0.60
    daily_stop_pct: float = 0.03
    max_orders: int = 5
    stop_loss_pct: float = 0.08
    trailing_stop_pct: float = 0.10
    kill_drawdown_pct: float = 0.20
    kill_benchmark_gap: float = 0.15
    ends: str = "2027-01-01"  # ISO date; runs skip once today >= ends

    @property
    def dir(self) -> Path:
        d = RUNS / self.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def keys(self) -> tuple[str, str] | None:
        k, s = os.environ.get(f"{self.key_env}_API_KEY"), os.environ.get(f"{self.key_env}_SECRET_KEY")
        return (k, s) if k and s else None

    @property
    def symbols(self) -> list[str]:
        return [s for syms in self.watchlist.values() for s in syms]

    @property
    def sector_of(self) -> dict[str, str]:
        return {s: sector for sector, syms in self.watchlist.items() for s in syms}

    @property
    def crypto(self) -> bool:
        return self.asset_class == "crypto"

    def canon(self, symbol: str) -> str:
        """Alpaca returns crypto positions as BTCUSD but quotes them as BTC/USD; map anything back to the watchlist spelling."""
        flat = symbol.replace("/", "")
        return next((s for s in self.symbols if s.replace("/", "") == flat), symbol)


STRATEGIES: dict[str, Strategy] = {
    name: Strategy(name=name, **cfg) for name, cfg in json.loads((ROOT / "strategies.json").read_text()).items()
}
