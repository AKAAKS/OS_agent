"""Tests for the action audit log + dry-run behavior."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentkernel.audit import ActionAuditLog, ActionRecord
from src.agent.graph import OODAAgent
from src.agent.tools import ActionResult
from src.observer.daemon import AnomalyType, ObservationEvent
from datetime import datetime


class TestActionAuditLog:

    def test_record_writes_jsonl(self, tmp_path):
        log = ActionAuditLog(path=tmp_path / "actions.jsonl")
        rec = ActionRecord(
            timestamp="2026-04-19T10:00:00",
            action="kill_process",
            args={"pid": 1234},
            dry_run=False,
            success=True,
            message="killed chrome.exe",
        )
        log.record(rec)

        content = (tmp_path / "actions.jsonl").read_text(encoding="utf-8")
        parsed = json.loads(content.strip())
        assert parsed["action"] == "kill_process"
        assert parsed["args"]["pid"] == 1234
        assert parsed["success"] is True

    def test_multiple_records_append(self, tmp_path):
        log = ActionAuditLog(path=tmp_path / "actions.jsonl")
        for i in range(3):
            log.record(ActionRecord(
                timestamp=f"2026-04-19T10:00:{i:02d}",
                action="kill_process",
                args={"pid": i},
                dry_run=False, success=True, message=f"#{i}",
            ))

        lines = (tmp_path / "actions.jsonl").read_text().strip().split("\n")
        assert len(lines) == 3

    def test_tail_returns_most_recent(self, tmp_path):
        log = ActionAuditLog(path=tmp_path / "actions.jsonl")
        for i in range(10):
            log.record(ActionRecord(
                timestamp=f"2026-04-19T10:00:{i:02d}",
                action="test", args={"i": i},
                dry_run=False, success=True, message="",
            ))

        tail = log.tail(n=3)
        assert len(tail) == 3
        assert [r["args"]["i"] for r in tail] == [7, 8, 9]

    def test_tail_ignores_garbage_lines(self, tmp_path):
        path = tmp_path / "actions.jsonl"
        path.write_text('{"action":"good"}\nnot-json\n{"action":"also-good"}\n')

        log = ActionAuditLog(path=path)
        tail = log.tail(n=10)
        assert [r["action"] for r in tail] == ["good", "also-good"]

    def test_empty_log_returns_empty_list(self, tmp_path):
        log = ActionAuditLog(path=tmp_path / "actions.jsonl")
        assert log.tail() == []


class TestDryRunMode:

    def _make_event(self) -> ObservationEvent:
        return ObservationEvent(
            anomaly_type=AnomalyType.CPU_SPIKE,
            summary="cpu 99%",
            severity="high",
            timestamp=datetime.now(),
            details={},
        )

    def _make_agent(self, tmp_path, dry_run: bool, profile_manager):
        from src.agent.tools import AgentTools
        tools = AgentTools()
        # Poison real kill so any accidental call fails loudly
        tools.kill_process = MagicMock(return_value=ActionResult(True, "killed"))
        audit = ActionAuditLog(path=tmp_path / "actions.jsonl")

        agent = OODAAgent(
            llm=MagicMock(),
            tools=tools,
            decision_engine=MagicMock(),
            profile_manager=profile_manager,
            settings={},
            dry_run=dry_run,
            audit_log=audit,
        )
        return agent, tools, audit

    def test_dry_run_skips_real_action(self, tmp_path, profile_manager):
        agent, tools, audit = self._make_agent(tmp_path, dry_run=True, profile_manager=profile_manager)

        state = {
            "planned_action": "kill_process",
            "action_args": {"pid": 1234},
            "risk_level": "high",
            "event": self._make_event(),
        }
        agent._act(state)

        tools.kill_process.assert_not_called()
        assert state["action_result"].success is True
        assert "dry-run" in state["action_result"].message

        records = audit.tail()
        assert len(records) == 1
        assert records[0]["dry_run"] is True
        assert records[0]["action"] == "kill_process"

    def test_live_mode_executes_and_audits(self, tmp_path, profile_manager):
        agent, tools, audit = self._make_agent(tmp_path, dry_run=False, profile_manager=profile_manager)

        state = {
            "planned_action": "kill_process",
            "action_args": {"pid": 1234},
            "risk_level": "high",
            "event": self._make_event(),
        }
        agent._act(state)

        tools.kill_process.assert_called_once_with(1234)
        records = audit.tail()
        assert len(records) == 1
        assert records[0]["dry_run"] is False
        assert records[0]["success"] is True
