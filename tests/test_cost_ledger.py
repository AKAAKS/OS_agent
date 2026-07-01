"""Tests for persistent daily cost ledger."""

import json
from datetime import datetime, timedelta

import pytest

from src.agent.cost_ledger import DailyCostLedger


@pytest.fixture
def ledger(tmp_path):
    return DailyCostLedger(daily_cap_usd=1.00, path=tmp_path / "ledger.json")


class TestDailyCostLedger:

    def test_initial_spend_is_zero(self, ledger):
        assert ledger.today_spend() == 0.0
        assert not ledger.cap_exceeded()
        assert ledger.remaining() == 1.00

    def test_add_accumulates(self, ledger):
        ledger.add(0.30)
        ledger.add(0.20)
        assert abs(ledger.today_spend() - 0.50) < 1e-9
        assert abs(ledger.remaining() - 0.50) < 1e-9
        assert not ledger.cap_exceeded()

    def test_cap_triggers_at_threshold(self, ledger):
        ledger.add(1.00)
        assert ledger.cap_exceeded()
        assert ledger.remaining() == 0.0

    def test_negative_or_zero_ignored(self, ledger):
        ledger.add(0.0)
        ledger.add(-0.50)
        assert ledger.today_spend() == 0.0

    def test_persistence_across_instances(self, tmp_path):
        path = tmp_path / "ledger.json"
        a = DailyCostLedger(daily_cap_usd=5.0, path=path)
        a.add(0.42)

        b = DailyCostLedger(daily_cap_usd=5.0, path=path)
        assert abs(b.today_spend() - 0.42) < 1e-9

    def test_corrupt_file_resets_gracefully(self, tmp_path):
        path = tmp_path / "ledger.json"
        path.write_text("{not valid json")
        ledger = DailyCostLedger(daily_cap_usd=1.0, path=path)
        assert ledger.today_spend() == 0.0
        # still usable
        ledger.add(0.1)
        assert abs(ledger.today_spend() - 0.1) < 1e-9

    def test_old_entries_gc(self, tmp_path):
        path = tmp_path / "ledger.json"
        # Write a ledger with entries 45 days old and today
        old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        path.write_text(json.dumps({old_date: 10.0, today: 0.0}))

        ledger = DailyCostLedger(daily_cap_usd=5.0, path=path)
        ledger.add(0.01)  # triggers gc + save

        reloaded = json.loads(path.read_text())
        assert old_date not in reloaded
        assert today in reloaded

    def test_history_returns_recent(self, tmp_path):
        path = tmp_path / "ledger.json"
        dates = {
            (datetime.now() - timedelta(days=d)).strftime("%Y-%m-%d"): float(d)
            for d in range(5)
        }
        path.write_text(json.dumps(dates))

        ledger = DailyCostLedger(daily_cap_usd=5.0, path=path)
        hist = ledger.history(days=3)
        assert len(hist) == 3
        # Most recent first
        keys = list(hist.keys())
        assert keys == sorted(keys, reverse=True)
