"""The HITL callback must fail closed when there is no console.

Under pythonw / Task Scheduler there is no human at a prompt, so every
gated action reaching the callback must be DENIED — never auto-approved.
(Regression test for the 2026-07-03 incident where a headless daemon
auto-approved kill_process on the user's running browser.)
"""

from datetime import datetime

import pytest

import main
from agentkernel.gate import ApprovalRequest
from agentkernel.risk import RiskLevel


@pytest.fixture
def headless_callback(monkeypatch):
    monkeypatch.setattr(main, "_has_console", lambda: False)
    return main.create_hitl_console_callback()


@pytest.mark.parametrize("risk", [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH])
def test_headless_denies_every_risk_level(headless_callback, risk):
    request = ApprovalRequest(
        action="kill_process",
        target="pid 12345",
        risk_level=risk,
        reason="test",
        timestamp=datetime.now(),
    )
    assert headless_callback(request) is False
