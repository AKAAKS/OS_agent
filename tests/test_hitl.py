"""Tests for HITL safety gate."""

import pytest

from src.safety.hitl import HITLGate, HITLRequest, RiskLevel


@pytest.fixture
def hitl_gate(sample_settings):
    return HITLGate(sample_settings)


class TestAutoApproval:

    def test_low_risk_auto_approved(self, hitl_gate):
        assert hitl_gate.check("kill_process", "chrome.exe", "high CPU") is True

    def test_medium_risk_needs_callback(self, hitl_gate):
        # No callback set — should deny
        assert hitl_gate.check("restart_service", "spooler", "stuck") is False

    def test_high_risk_denied_without_callback(self, hitl_gate):
        assert hitl_gate.check("run_command", "rm -rf /", "cleanup") is False


class TestCallback:

    def test_callback_approves(self, hitl_gate):
        hitl_gate.set_approval_callback(lambda req: True)
        assert hitl_gate.check("restart_service", "spooler", "stuck") is True

    def test_callback_denies(self, hitl_gate):
        hitl_gate.set_approval_callback(lambda req: False)
        assert hitl_gate.check("restart_service", "spooler", "stuck") is False

    def test_callback_receives_request_object(self, hitl_gate):
        received = []
        def capture(req):
            received.append(req)
            return True

        hitl_gate.set_approval_callback(capture)
        hitl_gate.check("run_command", "systeminfo", "diagnostics")

        assert len(received) == 1
        assert isinstance(received[0], HITLRequest)
        assert received[0].action == "run_command"
        assert received[0].risk_level == RiskLevel.HIGH


class TestHistory:

    def test_history_records_decisions(self, hitl_gate):
        hitl_gate.check("kill_process", "test.exe", "reason")
        history = hitl_gate.get_history()
        assert len(history) == 1
        assert history[0]["approved"] is True
        assert history[0]["auto"] is True

    def test_history_tracks_denials(self, hitl_gate):
        hitl_gate.check("run_command", "cmd", "reason")
        history = hitl_gate.get_history()
        assert len(history) == 1
        assert history[0]["approved"] is False
