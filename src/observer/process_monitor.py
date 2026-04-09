"""
Per-process resource tracker — detects memory leaks and unknown resource hogs.
Tracks each process's resource usage over time windows.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

import psutil

from src.observer.daemon import AnomalyType, ObservationEvent

logger = logging.getLogger(__name__)


@dataclass
class ProcessHistory:
    pid: int
    name: str
    memory_samples: list[tuple[datetime, float]]  # (timestamp, memory_mb)
    first_seen: datetime
    last_seen: datetime


class ProcessMonitor:
    """Tracks per-process memory over time to detect leaks and hogs."""

    def __init__(self, settings: dict):
        monitoring = settings.get("monitoring", {})
        self.leak_growth_mb = monitoring.get("memory_leak_growth_mb", 500)
        self.leak_window_min = monitoring.get("memory_leak_window_minutes", 30)
        self.max_samples = 360  # ~30 min at 5s intervals

        self._history: dict[int, ProcessHistory] = {}
        self._alerted_pids: set[int] = set()

    def update(self, top_processes: list[dict]) -> list[ObservationEvent]:
        """Call with each snapshot's top_processes. Returns any new anomaly events."""
        now = datetime.now()
        events = []
        current_pids = set()

        for proc in top_processes:
            pid = proc.get("pid")
            name = proc.get("name", "unknown")
            mem_mb = proc.get("memory_mb", 0)
            if pid is None:
                continue
            current_pids.add(pid)

            if pid not in self._history:
                self._history[pid] = ProcessHistory(
                    pid=pid,
                    name=name,
                    memory_samples=[(now, mem_mb)],
                    first_seen=now,
                    last_seen=now,
                )
            else:
                h = self._history[pid]
                h.memory_samples.append((now, mem_mb))
                h.last_seen = now
                # Trim old samples
                if len(h.memory_samples) > self.max_samples:
                    h.memory_samples = h.memory_samples[-self.max_samples :]

                # Check for memory leak
                leak_event = self._check_memory_leak(h, now)
                if leak_event:
                    events.append(leak_event)

        # Clean up processes that are no longer in the top list
        stale_pids = [
            pid
            for pid in self._history
            if pid not in current_pids
            and (now - self._history[pid].last_seen) > timedelta(minutes=5)
        ]
        for pid in stale_pids:
            del self._history[pid]
            self._alerted_pids.discard(pid)

        return events

    def _check_memory_leak(
        self, h: ProcessHistory, now: datetime
    ) -> ObservationEvent | None:
        if h.pid in self._alerted_pids:
            return None

        window_start = now - timedelta(minutes=self.leak_window_min)
        relevant = [(t, m) for t, m in h.memory_samples if t >= window_start]

        if len(relevant) < 10:  # Need enough data points
            return None

        oldest_mem = relevant[0][1]
        newest_mem = relevant[-1][1]
        growth = newest_mem - oldest_mem

        if growth >= self.leak_growth_mb:
            self._alerted_pids.add(h.pid)
            return ObservationEvent(
                timestamp=now,
                anomaly_type=AnomalyType.MEMORY_LEAK,
                severity="high",
                summary=(
                    f"{h.name} (PID {h.pid}) grew {growth:.0f} MB "
                    f"in {self.leak_window_min} min ({oldest_mem:.0f} → {newest_mem:.0f} MB)"
                ),
                details={
                    "pid": h.pid,
                    "name": h.name,
                    "growth_mb": round(growth, 1),
                    "start_mb": round(oldest_mem, 1),
                    "current_mb": round(newest_mem, 1),
                    "window_minutes": self.leak_window_min,
                },
            )
        return None

    def get_process_summary(self) -> list[dict]:
        """Get current tracked processes and their memory trends."""
        summaries = []
        for h in self._history.values():
            if len(h.memory_samples) < 2:
                continue
            oldest = h.memory_samples[0][1]
            newest = h.memory_samples[-1][1]
            summaries.append(
                {
                    "pid": h.pid,
                    "name": h.name,
                    "current_mb": round(newest, 1),
                    "change_mb": round(newest - oldest, 1),
                    "tracked_seconds": (h.last_seen - h.first_seen).total_seconds(),
                }
            )
        summaries.sort(key=lambda s: s["current_mb"], reverse=True)
        return summaries
