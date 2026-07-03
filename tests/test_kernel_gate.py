"""Tests for the fail-closed ApprovalGate.

Subsumes tests/test_hitl.py (auto-approval, callback, history) and the
integration-level gate behavior (always-gated actions, severity merge,
fail-closed on missing/raising callback).
"""

import pytest

from agentkernel.gate import ApprovalGate, ApprovalRequest
from agentkernel.risk import RiskLevel

RISK_MAP = {
    "kill_process": RiskLevel.LOW,
    "restart_service": RiskLevel.MEDIUM,
    "run_command": RiskLevel.HIGH,
}


@pytest.fixture
def gate():
    return ApprovalGate(
        risk_map=RISK_MAP,
        high_risk_actions={"run_command", "modify_registry"},
        auto_approve_levels={RiskLevel.LOW},
    )


class TestAutoApproval:
    def test_low_risk_ungated_action_auto_approved(self, gate):
        decision = gate.evaluate("kill_process")
        assert decision.approved is True
        assert decision.auto is True
        assert decision.risk is RiskLevel.LOW

    def test_medium_risk_denied_without_callback(self, gate):
        decision = gate.evaluate("restart_service")
        assert decision.approved is False
        assert decision.auto is False

    def test_high_risk_denied_without_callback(self, gate):
        assert gate.evaluate("run_command").approved is False


class TestAlwaysGatedActions:
    def test_gated_action_never_auto_approved_even_at_low_upstream(self, gate):
        seen = []
        gate.set_approval_callback(lambda req: seen.append(req) or True)
        decision = gate.evaluate("run_command", upstream_risk=RiskLevel.LOW)
        assert len(seen) == 1, "callback must be consulted for gated actions"
        assert decision.auto is False

    def test_forced_low_upstream_cannot_downgrade_canonical_risk(self, gate):
        seen = []
        gate.set_approval_callback(lambda req: seen.append(req) or True)
        gate.evaluate("run_command", upstream_risk=RiskLevel.LOW)
        assert seen[0].risk_level is RiskLevel.HIGH

    def test_upstream_high_never_downgraded_by_canonical(self):
        gate = ApprovalGate(
            risk_map={"odd_action": RiskLevel.LOW},
            high_risk_actions={"odd_action"},
            auto_approve_levels={RiskLevel.LOW},
        )
        seen = []
        gate.set_approval_callback(lambda req: seen.append(req) or True)
        gate.evaluate("odd_action", upstream_risk=RiskLevel.HIGH)
        assert seen[0].risk_level is RiskLevel.HIGH

    def test_unknown_action_defaults_to_high_risk(self, gate):
        decision = gate.evaluate("never_heard_of_it")
        assert decision.approved is False
        assert decision.risk is RiskLevel.HIGH


class TestCallback:
    def test_callback_approves(self, gate):
        gate.set_approval_callback(lambda req: True)
        assert gate.evaluate("restart_service").approved is True

    def test_callback_denies(self, gate):
        gate.set_approval_callback(lambda req: False)
        assert gate.evaluate("restart_service").approved is False

    def test_callback_receives_request_object(self, gate):
        received = []

        def capture(req):
            received.append(req)
            return True

        gate.set_approval_callback(capture)
        gate.evaluate("run_command", target="systeminfo", reason="diagnostics")

        assert len(received) == 1
        assert isinstance(received[0], ApprovalRequest)
        assert received[0].action == "run_command"
        assert received[0].target == "systeminfo"
        assert received[0].risk_level is RiskLevel.HIGH

    def test_raising_callback_denies(self, gate):
        def explode(req):
            raise RuntimeError("UI crashed")

        gate.set_approval_callback(explode)
        assert gate.evaluate("run_command").approved is False


class TestHistory:
    def test_history_records_auto_approval(self, gate):
        gate.evaluate("kill_process", target="test.exe")
        history = gate.get_history()
        assert len(history) == 1
        assert history[0]["approved"] is True
        assert history[0]["auto"] is True
        assert history[0]["action"] == "kill_process"

    def test_history_records_denials(self, gate):
        gate.evaluate("run_command", target="cmd")
        history = gate.get_history()
        assert len(history) == 1
        assert history[0]["approved"] is False
        assert history[0]["risk"] == "high"
