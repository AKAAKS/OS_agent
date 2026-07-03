"""
Persistent per-day LLM cost ledger.

Tracks spend across process restarts so the daily cap isn't reset every time
the agent launches. Stored as a single JSON file keyed by date (YYYY-MM-DD).
Old days are garbage collected after 30 days.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_RETENTION_DAYS = 30


class DailyCostLedger:
    """Thread-safe, file-backed daily LLM cost accumulator."""

    def __init__(self, daily_cap_usd: float, path: Path | str = "data/cost_ledger.json"):
        self.daily_cap_usd = float(daily_cap_usd)
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, float] = {}
        self._load()

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float))}
        except (json.JSONDecodeError, ValueError, OSError) as e:
            logger.warning(f"Cost ledger at {self.path} unreadable ({e}); starting fresh")
            self._data = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            logger.error(f"Failed to persist cost ledger to {self.path}: {e}")

    def _gc(self) -> None:
        """Drop entries older than RETENTION_DAYS."""
        cutoff = (datetime.now() - timedelta(days=_RETENTION_DAYS)).strftime("%Y-%m-%d")
        self._data = {k: v for k, v in self._data.items() if k >= cutoff}

    def add(self, cost_usd: float) -> None:
        """Record additional spend for today."""
        if cost_usd <= 0:
            return
        with self._lock:
            today = self._today()
            self._data[today] = self._data.get(today, 0.0) + float(cost_usd)
            self._gc()
            self._save()

    def today_spend(self) -> float:
        with self._lock:
            return self._data.get(self._today(), 0.0)

    def cap_exceeded(self) -> bool:
        return self.today_spend() >= self.daily_cap_usd

    def remaining(self) -> float:
        return max(0.0, self.daily_cap_usd - self.today_spend())

    def history(self, days: int = 7) -> dict[str, float]:
        """Recent daily spend, most-recent-first."""
        with self._lock:
            keys = sorted(self._data.keys(), reverse=True)[:days]
            return {k: self._data[k] for k in keys}
