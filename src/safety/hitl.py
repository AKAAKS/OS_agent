"""
Human-in-the-loop safety gate.
Classifies actions by risk level and prompts for approval when needed.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class HITLRequest:
    action: str
    target: str
    risk_level: RiskLevel
    reason: str
    timestamp: datetime


class HITLGate:
    """
    Safety gate that classifies actions and blocks high-risk ones
    until the user approves.
    """

    # Actions and their default risk levels
    RISK_MAP = {
        "kill_process": RiskLevel.LOW,
        "set_priority": RiskLevel.LOW,
        "trim_working_sets": RiskLevel.LOW,
        "restart_service": RiskLevel.MEDIUM,
        "defer_windows_update": RiskLevel.MEDIUM,
        "run_command": RiskLevel.HIGH,
        "modify_power_settings": RiskLevel.HIGH,
        "modify_registry": RiskLevel.HIGH,
        "modify_driver": RiskLevel.HIGH,
        "restart_bt_adapter": RiskLevel.MEDIUM,
        "disable_bt_power_saving": RiskLevel.MEDIUM,
    }

    def __init__(self, settings: dict):
        self.auto_approve_levels = {
            RiskLevel(level)
            for level in settings.get("safety", {}).get("auto_approve_risk_levels", ["low"])
        }
        self._pending_requests: list[HITLRequest] = []
        self._approval_callback = None
        self._history: list[dict] = []

    def set_approval_callback(self, callback):
        """Set a callback for user approval.
        callback(request: HITLRequest) -> bool
        """
        self._approval_callback = callback

    def check(self, action: str, target: str, reason: str) -> bool:
        """
        Check if an action should proceed.
        Returns True if approved, False if denied.
        """
        risk = self.RISK_MAP.get(action, RiskLevel.MEDIUM)
        request = HITLRequest(
            action=action,
            target=target,
            risk_level=risk,
            reason=reason,
            timestamp=datetime.now(),
        )

        # Auto-approve low-risk actions
        if risk in self.auto_approve_levels:
            self._log_decision(request, approved=True, auto=True)
            return True

        # Need human approval
        logger.info(f"HITL approval needed: {action} on {target} (risk: {risk.value})")

        if self._approval_callback:
            approved = self._approval_callback(request)
            self._log_decision(request, approved=approved, auto=False)
            return approved

        # No callback set — deny by default for safety
        logger.warning(f"HITL denied (no callback): {action} on {target}")
        self._log_decision(request, approved=False, auto=False)
        return False

    def _log_decision(self, request: HITLRequest, approved: bool, auto: bool):
        self._history.append({
            "action": request.action,
            "target": request.target,
            "risk": request.risk_level.value,
            "approved": approved,
            "auto": auto,
            "timestamp": request.timestamp.isoformat(),
        })

    def get_history(self) -> list[dict]:
        return list(self._history)
