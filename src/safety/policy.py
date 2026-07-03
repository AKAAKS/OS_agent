"""OS-domain safety policy: canonical action risks + gate construction.

The kernel's ApprovalGate is domain-agnostic; this module binds it to the
OS agent's action vocabulary and the settings.yaml safety schema.
"""

from agentkernel.gate import ApprovalGate
from agentkernel.risk import RiskLevel

# Canonical risk for every action the agent can emit or a profile can
# trigger. tests/test_policy.py enforces completeness.
RISK_MAP: dict[str, RiskLevel] = {
    "kill_process": RiskLevel.LOW,
    "set_priority": RiskLevel.LOW,
    "trim_working_sets": RiskLevel.LOW,
    "cleanup_temp_files": RiskLevel.LOW,
    "find_large_files": RiskLevel.LOW,
    "restart_service": RiskLevel.MEDIUM,
    "defer_windows_update": RiskLevel.MEDIUM,
    "restart_bt_adapter": RiskLevel.MEDIUM,
    "disable_bt_power_saving": RiskLevel.MEDIUM,
    "run_command": RiskLevel.HIGH,
    "modify_power_settings": RiskLevel.HIGH,
    "modify_registry": RiskLevel.HIGH,
    "modify_driver": RiskLevel.HIGH,
}


def build_approval_gate(settings: dict) -> ApprovalGate:
    """Map settings.yaml's `safety:` section onto a kernel ApprovalGate.

    Unknown actions default to HIGH (stricter than the old HITLGate's
    MEDIUM): anything we haven't classified must face a human. Invalid
    risk-level strings in config raise ValueError — fail fast at startup,
    not silently at approval time.
    """
    safety = settings.get("safety", {})
    return ApprovalGate(
        risk_map=RISK_MAP,
        high_risk_actions=set(safety.get("high_risk_actions", [])),
        auto_approve_levels={
            RiskLevel(level) for level in safety.get("auto_approve_risk_levels", ["low"])
        },
        default_risk=RiskLevel.HIGH,
    )
