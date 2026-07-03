"""
Action audit log — records every system-modifying action to a JSONL file.

Each line is a self-contained JSON record with enough context to reconstruct
what happened and why, without depending on a separate log. Used for:
  - post-hoc analysis of agent behavior
  - the GUI actions-log tab
  - dry-run verification
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ActionRecord:
    timestamp: str
    action: str
    args: dict[str, Any]
    dry_run: bool
    success: bool
    message: str
    risk_level: str = "unknown"
    event_type: str = ""
    event_summary: str = ""
    profile: str = ""
    duration_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


class ActionAuditLog:
    """Thread-safe append-only JSONL audit log."""

    def __init__(self, path: Path | str = "data/actions.jsonl"):
        self.path = Path(path)
        self._lock = threading.Lock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create audit log dir {self.path.parent}: {e}")

    def record(self, record: ActionRecord) -> None:
        """Append a record to the audit log. Failures are logged but never raised."""
        line = json.dumps(asdict(record), default=str)
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                logger.error(f"Failed to write audit record to {self.path}: {e}")

    def tail(self, n: int = 50) -> list[dict]:
        """Return the last N records as dicts (most recent last). Used by the GUI."""
        with self._lock:
            if not self.path.exists():
                return []
            try:
                with open(self.path, encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                return []

        records: list[dict] = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
