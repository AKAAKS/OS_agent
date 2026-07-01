"""
End-to-end integration tests for the OODA loop.

These exercise the full graph (Orient → Decide → HITL gate → Act → Verify) with
mocked psutil/WMI and a stubbed LLM. Unit tests elsewhere test each node in
isolation; these confirm they compose correctly.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.agent.audit import ActionAuditLog
from src.agent.graph import OODAAgent
from src.agent.llm import LLMResponse
from src.agent.tools import ActionResult, AgentTools
from src.observer.daemon import AnomalyType, ObservationEvent


def _event(anomaly_type: AnomalyType, summary: str, details: dict | None = None) -> ObservationEvent:
    return ObservationEvent(
        timestamp=datetime.now(),
        anomaly_type=anomaly_type,
        severity="high",
        summary=summary,
        details=details or {},
    )


@pytest.fixture
def stub_llm():
    llm = MagicMock()
    # Default: advisory response (should fall through to skip)
    llm.diagnose_anomaly.return_value = LLMResponse(
        content=json.dumps({
            "root_cause": "process X is misbehaving",
            "fix": "end task process X",
            "risk_level": "low",
            "confidence": 0.9,
        }),
        model_used="gpt-5-mini",
        input_tokens=100, output_tokens=50, confidence=0.9,
    )
    return llm


@pytest.fixture
def stub_decision_engine():
    from src.decisions.engine import Decision, ProcessDecision
    engine = MagicMock()
    engine.decide.return_value = ProcessDecision(
        process_name="stub", pid=0, decision=Decision.ASK,
        reason="no history", layer="stub", confidence=0.0,
    )
    return engine


@pytest.fixture
def settings():
    return {
        "api": {"confidence_threshold": 0.7},
        "safety": {
            "high_risk_actions": ["kill_process", "restart_service", "run_command"],
            "auto_approve_risk_levels": ["low"],
        },
    }


@pytest.fixture
def agent_factory(tmp_path, stub_llm, stub_decision_engine, profile_manager, settings):
    """Factory that builds an OODAAgent with stubbed externals."""
    def _build(dry_run: bool = False, hitl_approves: bool = True, tools: AgentTools | None = None):
        tools = tools or AgentTools()
        tools.kill_process = MagicMock(return_value=ActionResult(True, "killed"))
        tools.set_process_priority = MagicMock(return_value=ActionResult(True, "priority set"))
        tools.cleanup_temp_files = MagicMock(return_value=ActionResult(True, "cleaned"))
        tools.find_large_files = MagicMock(return_value=ActionResult(True, "found"))

        agent = OODAAgent(
            llm=stub_llm,
            tools=tools,
            decision_engine=stub_decision_engine,
            profile_manager=profile_manager,
            settings=settings,
            dry_run=dry_run,
            audit_log=ActionAuditLog(path=tmp_path / "actions.jsonl"),
        )
        agent.set_hitl_callback(lambda req: hitl_approves)
        return agent, tools
    return _build


class TestCpuSpikeFlow:

    def test_cpu_spike_kills_top_process(self, agent_factory, stub_llm):
        agent, tools = agent_factory()
        stub_llm.diagnose_anomaly.return_value = LLMResponse(
            content=json.dumps({
                "root_cause": "runaway chrome tab",
                "fix": "kill the top cpu process",
                "risk_level": "low",
                "confidence": 0.95,
            }),
            model_used="gpt-5-mini", input_tokens=100, output_tokens=50, confidence=0.95,
        )
        event = _event(AnomalyType.CPU_SPIKE, "cpu 98%", details={
            "top_processes": [{"pid": 1234, "name": "hog.exe", "cpu_percent": 98, "memory_mb": 200}],
        })

        result = agent.process_event(event)

        assert result["planned_action"] == "kill_process"
        assert result["action_args"]["pid"] == 1234
        tools.kill_process.assert_called_once_with(1234)
        assert result["completed"] is True

    def test_cpu_spike_advisory_fix_is_skipped(self, agent_factory, stub_llm):
        agent, tools = agent_factory()
        stub_llm.diagnose_anomaly.return_value = LLMResponse(
            content=json.dumps({
                "root_cause": "general system load",
                "fix": "consider upgrading hardware",
                "risk_level": "low",
                "confidence": 0.9,
            }),
            model_used="gpt-5-mini", input_tokens=100, output_tokens=50, confidence=0.9,
        )
        event = _event(AnomalyType.CPU_SPIKE, "cpu 98%", details={
            "top_processes": [{"pid": 1234, "name": "firefox.exe", "cpu_percent": 98, "memory_mb": 200}],
        })

        result = agent.process_event(event)

        assert result["planned_action"] == "skip"
        tools.kill_process.assert_not_called()


class TestHitlGate:

    def test_high_risk_action_blocked_when_hitl_denies(self, agent_factory, stub_llm):
        agent, tools = agent_factory(hitl_approves=False)
        stub_llm.diagnose_anomaly.return_value = LLMResponse(
            content=json.dumps({
                "root_cause": "x", "fix": "kill the top process",
                "risk_level": "high", "confidence": 0.9,
            }),
            model_used="gpt-5-mini", input_tokens=100, output_tokens=50, confidence=0.9,
        )
        event = _event(AnomalyType.CPU_SPIKE, "cpu 98%", details={
            "top_processes": [{"pid": 9, "name": "bad.exe", "cpu_percent": 99, "memory_mb": 100}],
        })

        agent.process_event(event)

        # High-risk + HITL denied → tools.kill_process must not be invoked
        tools.kill_process.assert_not_called()

    def test_low_risk_auto_approved(self, agent_factory, stub_llm):
        agent, tools = agent_factory(hitl_approves=False)  # would deny, but low risk auto-approves
        stub_llm.diagnose_anomaly.return_value = LLMResponse(
            content=json.dumps({
                "root_cause": "x", "fix": "kill the top process",
                "risk_level": "low", "confidence": 0.9,
            }),
            model_used="gpt-5-mini", input_tokens=100, output_tokens=50, confidence=0.9,
        )
        event = _event(AnomalyType.CPU_SPIKE, "cpu 98%", details={
            "top_processes": [{"pid": 9, "name": "bad.exe", "cpu_percent": 99, "memory_mb": 100}],
        })

        agent.process_event(event)
        tools.kill_process.assert_called_once()


class TestDryRunIntegration:

    def test_dry_run_blocks_execution_and_audits(self, agent_factory, stub_llm, tmp_path):
        agent, tools = agent_factory(dry_run=True)
        stub_llm.diagnose_anomaly.return_value = LLMResponse(
            content=json.dumps({
                "root_cause": "x", "fix": "kill top process",
                "risk_level": "low", "confidence": 0.9,
            }),
            model_used="gpt-5-mini", input_tokens=100, output_tokens=50, confidence=0.9,
        )
        event = _event(AnomalyType.CPU_SPIKE, "cpu 98%", details={
            "top_processes": [{"pid": 7, "name": "hog.exe", "cpu_percent": 99, "memory_mb": 100}],
        })

        agent.process_event(event)

        tools.kill_process.assert_not_called()
        records = agent.audit_log.tail()
        assert len(records) == 1
        assert records[0]["dry_run"] is True
        assert records[0]["action"] == "kill_process"


class TestDiskPressureFlow:

    def test_high_disk_triggers_cleanup(self, agent_factory):
        agent, tools = agent_factory()
        event = _event(AnomalyType.DISK_PRESSURE, "disk 96%", details={"disk_percent": 96})

        result = agent.process_event(event)

        assert result["planned_action"] == "cleanup_temp_files"
        tools.cleanup_temp_files.assert_called_once()

    def test_moderate_disk_triggers_find_only(self, agent_factory):
        agent, tools = agent_factory()
        event = _event(AnomalyType.DISK_PRESSURE, "disk 91%", details={"disk_percent": 91})

        result = agent.process_event(event)

        assert result["planned_action"] == "find_large_files"
        tools.find_large_files.assert_called_once()
        tools.cleanup_temp_files.assert_not_called()


class TestThermalAndGpuFlow:

    def test_thermal_throttle_is_advisory_only(self, agent_factory):
        agent, tools = agent_factory()
        event = _event(AnomalyType.THERMAL_THROTTLE, "CPU 85C")

        result = agent.process_event(event)

        assert result["planned_action"] == "skip"
        tools.kill_process.assert_not_called()
        tools.cleanup_temp_files.assert_not_called()

    def test_gpu_high_is_advisory_only(self, agent_factory):
        agent, tools = agent_factory()
        event = _event(AnomalyType.GPU_HIGH, "gpu 99%")

        result = agent.process_event(event)

        assert result["planned_action"] == "skip"
