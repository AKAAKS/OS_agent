"""Tests for the OS-domain safety policy binding."""

from agentkernel.risk import RiskLevel
from src.safety.policy import RISK_MAP, build_approval_gate

# Every action name the OODA agent's decide/act stages can emit, plus the
# profile-triggered actions. If you add an action to graph.py or main.py,
# it MUST get a canonical risk here.
EMITTED_ACTIONS = {
    "kill_process", "set_priority", "restart_service", "run_command",
    "cleanup_temp_files", "find_large_files", "trim_working_sets",
    "defer_windows_update",
}


def test_every_emitted_action_has_a_canonical_risk():
    missing = EMITTED_ACTIONS - set(RISK_MAP)
    assert not missing, f"actions without canonical risk: {missing}"


def test_run_command_is_canonically_high():
    assert RISK_MAP["run_command"] is RiskLevel.HIGH


def test_gate_from_settings_fails_closed_on_gated_action(sample_settings):
    gate = build_approval_gate(sample_settings)
    # run_command is in sample_settings' high_risk_actions; no callback wired.
    decision = gate.evaluate("run_command", upstream_risk=RiskLevel.LOW)
    assert decision.approved is False


def test_gate_from_settings_auto_approves_ungated_low(sample_settings):
    gate = build_approval_gate(sample_settings)
    decision = gate.evaluate("kill_process", upstream_risk=RiskLevel.LOW)
    assert decision.approved is True
    assert decision.auto is True


def test_unknown_action_defaults_high(sample_settings):
    gate = build_approval_gate(sample_settings)
    assert gate.evaluate("brand_new_action").approved is False
