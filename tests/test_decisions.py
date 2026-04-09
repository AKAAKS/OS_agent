"""Tests for the 4-layer decision engine."""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.decisions.engine import DecisionEngine, Decision, ProcessDecision
from src.profiles.manager import ProfileType


@pytest.fixture
def decision_engine(profile_manager, sample_settings, tmp_path):
    """Decision engine with a temp DB."""
    db = str(tmp_path / "test_decisions.db")
    return DecisionEngine(profile_manager, sample_settings, db_path=db)


class TestLayerOneTwo:
    """Config + Profile rule layers."""

    def test_whitelist_returns_keep(self, decision_engine):
        result = decision_engine.decide("explorer.exe", pid=1234)
        assert result.decision == Decision.KEEP
        assert result.layer == "config"
        assert result.confidence == 1.0

    def test_blacklist_returns_kill(self, decision_engine, profile_manager):
        # Add to global blacklist
        profile_manager.global_blacklist.add("bloatware.exe")
        result = decision_engine.decide("bloatware.exe", pid=5678)
        assert result.decision == Decision.KILL
        assert result.layer == "config"

    def test_profile_keep(self, decision_engine, profile_manager):
        profile_manager._active_profile = ProfileType.CODING
        result = decision_engine.decide("code.exe", pid=100)
        assert result.decision == Decision.KEEP
        assert result.layer == "profile"

    def test_profile_kill(self, decision_engine, profile_manager):
        profile_manager._active_profile = ProfileType.CODING
        result = decision_engine.decide("widgets.exe", pid=200)
        assert result.decision == Decision.KILL
        assert result.layer == "profile"

    def test_unknown_returns_ask(self, decision_engine):
        result = decision_engine.decide("mystery.exe", pid=999)
        assert result.decision == Decision.ASK
        assert result.layer == "unknown"

    def test_none_process_name_returns_keep(self, decision_engine):
        """None/empty process name should be safely handled, not crash."""
        result = decision_engine.decide(None, pid=999)
        assert result.decision == Decision.KEEP
        assert result.layer == "safety"

    def test_empty_process_name_returns_keep(self, decision_engine):
        result = decision_engine.decide("", pid=999)
        assert result.decision == Decision.KEEP
        assert result.layer == "safety"


class TestLayerThreeHistory:
    """Behavioral history layer."""

    def test_no_history_returns_none(self, decision_engine):
        assert decision_engine._check_history("mystery.exe") is None

    def test_learns_after_n_consistent(self, decision_engine, sample_settings):
        n = sample_settings["decisions"]["auto_learn_after_n"]  # 3
        for _ in range(n):
            decision_engine.record_user_decision("mystery.exe", Decision.KILL)

        result = decision_engine._check_history("mystery.exe")
        assert result == Decision.KILL

    def test_mixed_history_below_threshold(self, decision_engine):
        """If decisions are split, don't auto-decide."""
        decision_engine.record_user_decision("ambiguous.exe", Decision.KILL)
        decision_engine.record_user_decision("ambiguous.exe", Decision.KEEP)
        decision_engine.record_user_decision("ambiguous.exe", Decision.KILL)
        # 2 kill vs 1 keep = 66% — below 75% threshold
        assert decision_engine._check_history("ambiguous.exe") is None

    def test_history_is_profile_scoped(self, decision_engine, profile_manager):
        """Decisions learned in gaming shouldn't auto-decide in coding."""
        profile_manager._active_profile = ProfileType.GAMING
        for _ in range(3):
            decision_engine.record_user_decision("app.exe", Decision.KILL)

        # In gaming, should auto-kill
        assert decision_engine._check_history("app.exe") == Decision.KILL

        # Switch to coding — no history there
        profile_manager._active_profile = ProfileType.CODING
        assert decision_engine._check_history("app.exe") is None


class TestDecisionStats:

    def test_empty_stats(self, decision_engine):
        assert decision_engine.get_decision_stats() == []

    def test_stats_reflect_recordings(self, decision_engine):
        decision_engine.record_user_decision("app.exe", Decision.KILL)
        decision_engine.record_user_decision("app.exe", Decision.KILL)
        stats = decision_engine.get_decision_stats()
        assert len(stats) == 1
        assert stats[0]["process"] == "app.exe"
        assert stats[0]["count"] == 2
