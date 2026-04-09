"""
4-layer process decision engine.
Layer 1: Config whitelist/blacklist (instant, free)
Layer 2: Profile rules (instant, free)
Layer 3: Behavioral history from SQLite (instant, free)
Layer 4: LLM analysis (API call, unknowns only) + HITL (ask user)
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class Decision(Enum):
    KEEP = "keep"
    KILL = "kill"
    ASK = "ask"


@dataclass
class ProcessDecision:
    process_name: str
    pid: int
    decision: Decision
    reason: str
    layer: str  # Which layer made the decision
    confidence: float  # 0.0 - 1.0


class DecisionEngine:
    """
    Makes kill/keep decisions for processes using a 4-layer hierarchy.
    Learns from user choices over time.
    """

    def __init__(self, profile_manager, settings: dict, db_path: str = "data/decisions.db"):
        self.profile_manager = profile_manager
        self.auto_learn_after = settings.get("decisions", {}).get("auto_learn_after_n", 3)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS process_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    process_name TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_process_profile
                ON process_decisions(process_name, profile)
            """)

    def decide(self, process_name: str, pid: int, context: dict | None = None) -> ProcessDecision:
        """
        Run through all layers to decide what to do with a process.
        Returns a ProcessDecision. If decision is ASK, the caller should
        prompt the user and then call record_user_decision().
        """
        if not process_name:
            return ProcessDecision(
                process_name="<unknown>", pid=pid,
                decision=Decision.KEEP, reason="Process name is None/empty — skipping",
                layer="safety", confidence=1.0,
            )
        # Layer 1 + 2: Config + Profile rules
        classification = self.profile_manager.classify_process(process_name)

        if classification == "whitelist":
            return ProcessDecision(
                process_name=process_name, pid=pid,
                decision=Decision.KEEP, reason="System-critical process (global whitelist)",
                layer="config", confidence=1.0,
            )
        if classification == "blacklist":
            return ProcessDecision(
                process_name=process_name, pid=pid,
                decision=Decision.KILL, reason="Known bloatware (global blacklist)",
                layer="config", confidence=1.0,
            )
        if classification == "keep":
            profile = self.profile_manager.active_profile.value
            return ProcessDecision(
                process_name=process_name, pid=pid,
                decision=Decision.KEEP, reason=f"Protected in {profile} profile",
                layer="profile", confidence=0.95,
            )
        if classification == "kill":
            profile = self.profile_manager.active_profile.value
            return ProcessDecision(
                process_name=process_name, pid=pid,
                decision=Decision.KILL, reason=f"Kill candidate in {profile} profile",
                layer="profile", confidence=0.9,
            )

        # Layer 3: Behavioral history
        history_decision = self._check_history(process_name)
        if history_decision:
            return ProcessDecision(
                process_name=process_name, pid=pid,
                decision=history_decision,
                reason=f"Learned from {self.auto_learn_after}+ consistent past choices",
                layer="history", confidence=0.85,
            )

        # Layer 4: Unknown — needs LLM or user input
        return ProcessDecision(
            process_name=process_name, pid=pid,
            decision=Decision.ASK,
            reason="Unknown process — not in any rule or history",
            layer="unknown", confidence=0.0,
        )

    def _check_history(self, process_name: str) -> Decision | None:
        """Check if we have enough consistent history to auto-decide."""
        profile = self.profile_manager.active_profile.value
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                rows = conn.execute(
                    """
                    SELECT decision, COUNT(*) as cnt
                    FROM process_decisions
                    WHERE process_name = ? AND profile = ?
                    GROUP BY decision
                    ORDER BY cnt DESC
                    """,
                    (process_name.lower(), profile),
                ).fetchall()
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.error(f"Decision DB query failed: {e}")
            return None

        if not rows:
            return None

        top_decision, top_count = rows[0]
        total = sum(r[1] for r in rows)

        # Need N consistent choices and >75% agreement
        if top_count >= self.auto_learn_after and (top_count / total) > 0.75:
            return Decision(top_decision)

        return None

    def record_user_decision(self, process_name: str, decision: Decision):
        """Record a user's keep/kill choice for future learning."""
        profile = self.profile_manager.active_profile.value
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.execute(
                    """
                    INSERT INTO process_decisions (process_name, profile, decision, timestamp)
                    VALUES (?, ?, ?, ?)
                    """,
                    (process_name.lower(), profile, decision.value, datetime.now().isoformat()),
                )
            logger.info(
                f"Recorded decision: {decision.value} for '{process_name}' in {profile} profile"
            )
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.error(f"Failed to record decision: {e}")

    def get_decision_stats(self) -> list[dict]:
        """Get summary of all learned decisions."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                rows = conn.execute("""
                    SELECT process_name, profile, decision, COUNT(*) as cnt
                    FROM process_decisions
                    GROUP BY process_name, profile, decision
                    ORDER BY process_name, profile
                """).fetchall()
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.error(f"Decision DB stats query failed: {e}")
            return []

        stats = []
        for name, profile, decision, count in rows:
            stats.append({
                "process": name,
                "profile": profile,
                "decision": decision,
                "count": count,
            })
        return stats
