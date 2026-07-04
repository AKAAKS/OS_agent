"""Shared fixtures for OS Agent tests."""

import pytest


@pytest.fixture
def sample_settings():
    """Minimal settings dict matching config/settings.yaml structure."""
    return {
        "api": {
            "provider": "openai",
            "api_key": "test-key-123",
            "triage_model": "gpt-4o-mini",
            "complex_model": "gpt-4o",
            "confidence_threshold": 0.7,
            "cost_cap_usd": 1.00,
        },
        "monitoring": {
            "poll_interval_seconds": 5,
            "poll_interval_battery_seconds": 30,
            "cpu_threshold_percent": 95,
            "cpu_sustained_seconds": 120,
            "ram_threshold_percent": 85,
            "disk_threshold_percent": 90,
            "cooldown_minutes": 10,
        },
        "safety": {
            "high_risk_actions": ["run_command", "modify_registry"],
            "auto_approve_risk_levels": ["low"],
        },
        "decisions": {
            "auto_learn_after_n": 3,
            "similarity_threshold": 0.78,
        },
        "logging": {
            "level": "INFO",
        },
    }


@pytest.fixture
def sample_profiles_config():
    """Minimal profiles config matching config/profiles.yaml structure."""
    return {
        "profiles": {
            "gaming": {
                "trigger_processes": ["steam.exe", "epicgameslauncher.exe"],
                "always_keep": ["discord.exe", "steam.exe", "explorer.exe"],
                "kill_candidates": ["onedrive.exe", "teams.exe"],
                "actions": {"set_game_priority": "high", "defer_windows_update": True},
            },
            "coding": {
                "trigger_processes": ["code.exe", "devenv.exe"],
                "always_keep": ["code.exe", "windowsterminal.exe", "python.exe", "explorer.exe"],
                "kill_candidates": ["widgets.exe"],
                "actions": {"defer_windows_update": True},
            },
            "streaming": {
                "trigger_processes": [],
                "always_keep": ["chrome.exe", "firefox.exe", "explorer.exe"],
                "kill_candidates": ["onedrive.exe"],
                "actions": {"trim_idle_working_sets": True},
            },
            "idle": {
                "trigger_processes": [],
                "always_keep": [],
                "kill_candidates": [],
                "actions": {},
            },
        },
        "global_whitelist": [
            "explorer.exe", "csrss.exe", "winlogon.exe", "services.exe",
            "svchost.exe", "lsass.exe", "dwm.exe", "System",
        ],
        "global_blacklist": [],
    }


@pytest.fixture
def profile_manager(sample_profiles_config):
    """Pre-built ProfileManager for tests.

    Input-idle is pinned to 0 ("user is active") so detection tests are
    deterministic regardless of the machine running them.
    """
    from src.profiles.manager import ProfileManager
    pm = ProfileManager(
        sample_profiles_config,
        global_whitelist=sample_profiles_config["global_whitelist"],
        global_blacklist=sample_profiles_config["global_blacklist"],
    )
    pm._get_input_idle_seconds = lambda: 0.0
    return pm
