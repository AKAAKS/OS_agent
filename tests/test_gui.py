"""Lightweight smoke tests for the PyQt6 tray GUI.

Tests construction + refresh logic only — the event loop is not started, so
these run in any environment with PyQt6 installed, including headless CI.
"""

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

# Offscreen platform so tests pass without a display / tray
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.agent.audit import ActionAuditLog, ActionRecord  # noqa: E402
from src.agent.cost_ledger import DailyCostLedger  # noqa: E402
from src.gui.tray import DetailsWindow, _make_icon  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_icon_factory(qapp):
    icon = _make_icon("#44cc44")
    assert not icon.isNull()


def test_details_window_builds_four_tabs(qapp, tmp_path):
    audit = ActionAuditLog(path=tmp_path / "actions.jsonl")
    ledger = DailyCostLedger(daily_cap_usd=1.0, path=tmp_path / "ledger.json")
    win = DetailsWindow(
        audit_log=audit,
        cost_ledger=ledger,
        config_paths={"settings.yaml": tmp_path / "settings.yaml"},
        crashes_dir=tmp_path / "crashes",
    )
    tabs = win.centralWidget()
    assert tabs.count() == 4
    assert [tabs.tabText(i) for i in range(4)] == ["Status", "Actions", "Crashes", "Settings"]


def test_details_window_renders_actions(qapp, tmp_path):
    audit = ActionAuditLog(path=tmp_path / "actions.jsonl")
    audit.record(ActionRecord(
        timestamp="2026-04-19T10:00:00",
        action="kill_process", args={"pid": 42},
        dry_run=False, success=True, message="killed it",
    ))
    audit.record(ActionRecord(
        timestamp="2026-04-19T10:01:00",
        action="cleanup_temp_files", args={},
        dry_run=True, success=True, message="would clean 500MB",
    ))

    ledger = DailyCostLedger(daily_cap_usd=1.0, path=tmp_path / "ledger.json")
    ledger.add(0.25)

    win = DetailsWindow(
        audit_log=audit, cost_ledger=ledger,
        config_paths={},
        crashes_dir=tmp_path / "crashes",
    )
    win.show()  # needed so refresh() runs
    win.refresh()
    # 2 audit rows rendered
    assert win._actions_table.rowCount() == 2
    # Cost label reflects the 0.25 spend
    assert "0.2500" in win._cost_label.text()


def test_details_window_renders_crashes(qapp, tmp_path):
    crashes = tmp_path / "crashes"
    crashes.mkdir()
    (crashes / "crash_20260419_100000_1.json").write_text(json.dumps({
        "timestamp": "2026-04-19T10:00:00",
        "exception_type": "ValueError",
        "exception_message": "bad input",
    }))

    audit = ActionAuditLog(path=tmp_path / "actions.jsonl")
    ledger = DailyCostLedger(daily_cap_usd=1.0, path=tmp_path / "ledger.json")
    win = DetailsWindow(audit, ledger, {}, crashes)
    win.show()
    win.refresh()
    assert win._crashes_table.rowCount() == 1
    assert win._crashes_table.item(0, 1).text() == "ValueError"
