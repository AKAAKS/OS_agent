"""Fail-closed approval gate for autonomous actions.

Domain-agnostic: the caller supplies the action vocabulary (risk_map), the
set of actions that must always face a human (high_risk_actions), and which
risk levels may auto-approve.

Two safety properties:

1. Actions in ``high_risk_actions`` ALWAYS go to the approval callback.
   An upstream risk label (which may be wrong, stale, or forced low by a
   caching layer) can never silently auto-approve them, and the risk shown
   to the human is the MORE severe of the upstream label and the action's
   canonical risk.
2. Fail closed: no callback wired means deny; a callback that raises means
   deny.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from agentkernel.risk import RiskLevel, max_severity

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    """What the human sees when asked to approve an action."""

    action: str
    target: str
    risk_level: RiskLevel
    reason: str
    timestamp: datetime


@dataclass
class GateDecision:
    approved: bool
    risk: RiskLevel  # effective risk after upstream/canonical merge
    auto: bool  # True = approved without consulting the callback
    reason: str = ""


ApprovalCallback = Callable[[ApprovalRequest], bool]


class ApprovalGate:
    """Fail-closed approval gate. See module docstring for the contract."""

    def __init__(
        self,
        risk_map: dict[str, RiskLevel],
        high_risk_actions: set[str] | frozenset[str] = frozenset(),
        auto_approve_levels: set[RiskLevel] | frozenset[RiskLevel] = frozenset({RiskLevel.LOW}),
        default_risk: RiskLevel = RiskLevel.HIGH,
    ):
        self.risk_map = dict(risk_map)
        self.high_risk_actions = set(high_risk_actions)
        self.auto_approve_levels = set(auto_approve_levels)
        self.default_risk = default_risk
        self._callback: ApprovalCallback | None = None
        self._history: list[dict] = []

    def set_approval_callback(self, callback: ApprovalCallback) -> None:
        """callback(request: ApprovalRequest) -> bool (True = approved)."""
        self._callback = callback

    def evaluate(
        self,
        action: str,
        target: str = "",
        reason: str = "",
        upstream_risk: RiskLevel | None = None,
    ) -> GateDecision:
        """Decide whether `action` may proceed. Never raises."""
        canonical = self.risk_map.get(action, self.default_risk)
        resolved = upstream_risk if upstream_risk is not None else canonical
        is_high = action in self.high_risk_actions

        if not is_high and resolved in self.auto_approve_levels:
            return self._record(
                action, target, GateDecision(True, resolved, auto=True, reason="auto-approved")
            )

        # Always-gated actions show the human the MORE severe of upstream
        # and canonical risk, so a forced-low label can't understate them.
        risk = max_severity(resolved, canonical) if is_high else resolved

        if self._callback is None:
            logger.warning(f"Approval needed but no callback set — denying '{action}'")
            return self._record(
                action, target,
                GateDecision(False, risk, auto=False, reason="no approval callback (fail closed)"),
            )

        request = ApprovalRequest(
            action=action or "unknown",
            target=target,
            risk_level=risk,
            reason=reason,
            timestamp=datetime.now(),
        )
        try:
            approved = bool(self._callback(request))
        except Exception as e:
            logger.error(f"Approval callback failed: {e} — denying '{action}'")
            return self._record(
                action, target,
                GateDecision(False, risk, auto=False, reason=f"callback error: {e}"),
            )

        return self._record(action, target, GateDecision(approved, risk, auto=False))

    def _record(self, action: str, target: str, decision: GateDecision) -> GateDecision:
        self._history.append({
            "action": action,
            "target": target,
            "risk": decision.risk.value,
            "approved": decision.approved,
            "auto": decision.auto,
            "reason": decision.reason,
            "timestamp": datetime.now().isoformat(),
        })
        return decision

    def get_history(self) -> list[dict]:
        return list(self._history)
