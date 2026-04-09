"""Tests for profile manager — detection, classification, debounce."""

import time
from unittest.mock import patch, MagicMock

import pytest

from src.profiles.manager import ProfileManager, ProfileType


class TestProfileClassification:
    """Test process classification against profiles."""

    def test_global_whitelist(self, profile_manager):
        assert profile_manager.classify_process("explorer.exe") == "whitelist"
        assert profile_manager.classify_process("svchost.exe") == "whitelist"
        assert profile_manager.classify_process("EXPLORER.EXE") == "whitelist"  # case-insensitive

    def test_global_whitelist_is_protected(self, profile_manager):
        assert profile_manager.should_process_be_protected("explorer.exe") is True
        assert profile_manager.should_process_be_protected("svchost.exe") is True

    def test_unknown_process(self, profile_manager):
        assert profile_manager.classify_process("random_app.exe") == "unknown"

    def test_profile_keep_in_coding(self, profile_manager):
        # Force coding profile
        profile_manager._active_profile = ProfileType.CODING
        assert profile_manager.classify_process("code.exe") == "keep"
        assert profile_manager.classify_process("python.exe") == "keep"

    def test_profile_kill_in_coding(self, profile_manager):
        profile_manager._active_profile = ProfileType.CODING
        assert profile_manager.classify_process("widgets.exe") == "kill"

    def test_profile_kill_in_gaming(self, profile_manager):
        profile_manager._active_profile = ProfileType.GAMING
        assert profile_manager.classify_process("onedrive.exe") == "kill"
        assert profile_manager.classify_process("teams.exe") == "kill"

    def test_gaming_keep_overrides_coding_kill(self, profile_manager):
        """Discord is keep in gaming, not in coding's kill list."""
        profile_manager._active_profile = ProfileType.GAMING
        assert profile_manager.classify_process("discord.exe") == "keep"

    def test_global_whitelist_beats_profile_kill(self, profile_manager):
        """Global whitelist should override profile kill candidates."""
        profile_manager._active_profile = ProfileType.GAMING
        assert profile_manager.classify_process("explorer.exe") == "whitelist"


class TestProfileDetection:
    """Test profile detection logic."""

    def test_default_is_idle(self, profile_manager):
        assert profile_manager.active_profile == ProfileType.IDLE

    @patch.object(ProfileManager, "_get_foreground_process_name", return_value="code.exe")
    def test_foreground_coding(self, mock_fg, profile_manager):
        # First call starts debounce
        profile_manager.detect_profile()
        # Force both entry and exit debounce to expire
        past = time.monotonic() - 120
        profile_manager._pending_since = past
        profile_manager._current_last_seen = past
        result = profile_manager.detect_profile()
        assert result == ProfileType.CODING

    @patch.object(ProfileManager, "_get_foreground_process_name", return_value="firefox.exe")
    def test_foreground_browsing(self, mock_fg, profile_manager):
        profile_manager.detect_profile()
        past = time.monotonic() - 120
        profile_manager._pending_since = past
        profile_manager._current_last_seen = past
        result = profile_manager.detect_profile()
        assert result == ProfileType.BROWSING

    @patch.object(ProfileManager, "_get_foreground_process_name", return_value="notepad.exe")
    @patch("psutil.process_iter", return_value=[])
    def test_unknown_foreground_falls_to_idle(self, mock_procs, mock_fg, profile_manager):
        """An unrecognized foreground app should fall back to idle."""
        result = profile_manager.detect_profile()
        assert result == ProfileType.IDLE


class TestDebounce:
    """Test the debounce mechanism prevents rapid switching."""

    @patch.object(ProfileManager, "_get_foreground_process_name")
    def test_debounce_prevents_immediate_switch(self, mock_fg, profile_manager):
        """Switching from idle to coding should not happen instantly."""
        mock_fg.return_value = "code.exe"
        result = profile_manager.detect_profile()
        # Should still be idle — debounce hasn't elapsed
        assert result == ProfileType.IDLE
        assert profile_manager._pending_profile == ProfileType.CODING

    @patch.object(ProfileManager, "_get_foreground_process_name")
    def test_debounce_resets_on_switch_back(self, mock_fg, profile_manager):
        """If user switches back before debounce expires, pending is cleared."""
        # Detect coding
        mock_fg.return_value = "code.exe"
        profile_manager.detect_profile()
        assert profile_manager._pending_profile == ProfileType.CODING

        # Switch back to idle (unrecognized foreground)
        mock_fg.return_value = None
        with patch("psutil.process_iter", return_value=[]):
            profile_manager.detect_profile()
        # Pending should be idle now, but since current IS idle, pending cleared
        assert profile_manager._pending_profile is None
        assert profile_manager.active_profile == ProfileType.IDLE

    @patch.object(ProfileManager, "_get_foreground_process_name")
    def test_debounce_commits_after_timeout(self, mock_fg, profile_manager):
        """Profile should switch after debounce period elapses."""
        mock_fg.return_value = "code.exe"
        profile_manager.detect_profile()

        # Simulate both entry debounce and exit debounce elapsed
        past = time.monotonic() - 120  # 2 minutes ago — exceeds any exit debounce
        profile_manager._pending_since = past
        profile_manager._current_last_seen = past  # current profile unseen long enough

        result = profile_manager.detect_profile()
        assert result == ProfileType.CODING
        assert profile_manager.active_profile == ProfileType.CODING
        assert profile_manager._pending_profile is None


class TestGetKillCandidates:

    def test_gaming_kill_candidates(self, profile_manager):
        profile_manager._active_profile = ProfileType.GAMING
        candidates = profile_manager.get_kill_candidates()
        assert "onedrive.exe" in candidates
        assert "teams.exe" in candidates

    def test_idle_no_kill_candidates(self, profile_manager):
        candidates = profile_manager.get_kill_candidates()
        assert candidates == []
