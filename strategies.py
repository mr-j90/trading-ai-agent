"""Strategy definitions. Source of truth is strategies.json (shared with the dashboard)."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent
RUNS = ROOT / "runs"


@dataclass(frozen=True)
class Strategy:
    name: str
    key_env: str  # env prefix: <key_env>_API_KEY / <key_env>_SECRET_KEY for that paper account
    description: str = ""
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


STRATEGIES: dict[str, Strategy] = {
    name: Strategy(name=name, **cfg) for name, cfg in json.loads((ROOT / "strategies.json").read_text()).items()
}
