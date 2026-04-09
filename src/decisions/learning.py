"""
Behavioral learning — tracks user decisions over time and provides
auto-decision capabilities based on patterns.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class BehavioralLearner:
    """
    Extends the decision engine with richer behavioral tracking.
    Stores context alongside decisions for more nuanced learning.
    """

    def __init__(self, db_path: str = "data/decisions.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _init_tables(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS process_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    process_name TEXT NOT NULL,
                    pid INTEGER,
                    cpu_percent REAL,
                    memory_mb REAL,
                    parent_process TEXT,
                    profile TEXT,
                    user_decision TEXT,
                    timestamp TEXT NOT NULL
                )
            """)

    def record_with_context(
        self,
        process_name: str,
        pid: int,
        cpu_percent: float,
        memory_mb: float,
        parent_process: str,
        profile: str,
        decision: str,
    ):
        """Record a decision with full process context for richer learning."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.execute(
                    """
                    INSERT INTO process_context
                    (process_name, pid, cpu_percent, memory_mb, parent_process, profile, user_decision, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        process_name.lower(),
                        pid,
                        cpu_percent,
                        memory_mb,
                        parent_process,
                        profile,
                        decision,
                        datetime.now().isoformat(),
                    ),
                )
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.error(f"Failed to record process context: {e}")

    def get_process_history(self, process_name: str) -> list[dict]:
        """Get all past decisions for a specific process."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                rows = conn.execute(
                    """
                    SELECT cpu_percent, memory_mb, parent_process, profile, user_decision, timestamp
                    FROM process_context
                    WHERE process_name = ?
                    ORDER BY timestamp DESC
                    LIMIT 20
                    """,
                    (process_name.lower(),),
                ).fetchall()
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.error(f"Failed to query process history: {e}")
            return []

        return [
            {
                "cpu_percent": r[0],
                "memory_mb": r[1],
                "parent_process": r[2],
                "profile": r[3],
                "decision": r[4],
                "timestamp": r[5],
            }
            for r in rows
        ]

    def build_llm_context(self, process_name: str) -> str:
        """Build a context string for the LLM about a process's history."""
        history = self.get_process_history(process_name)
        if not history:
            return f"No prior history for '{process_name}'."

        lines = [f"History for '{process_name}' ({len(history)} past encounters):"]
        for h in history[:5]:  # Last 5 for token efficiency
            lines.append(
                f"  - {h['decision']} | Profile: {h['profile']} | "
                f"CPU: {h['cpu_percent']:.1f}% | RAM: {h['memory_mb']:.0f}MB | "
                f"Parent: {h['parent_process']}"
            )
        return "\n".join(lines)
