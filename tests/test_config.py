"""Tests for config validation."""

import pytest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import validate_config


class TestConfigValidation:

    def test_valid_config_no_warnings(self, sample_settings, sample_profiles_config):
        warnings = validate_config(sample_settings, sample_profiles_config)
        # Only the API key warning since our test key isn't an env var
        api_warnings = [w for w in warnings if "api_key" not in w.lower() and "OPENAI_API_KEY" not in w]
        assert api_warnings == []

    def test_missing_api_section(self, sample_profiles_config):
        warnings = validate_config({}, sample_profiles_config)
        assert any("missing 'api'" in w for w in warnings)

    def test_missing_monitoring_section(self, sample_profiles_config):
        settings = {"api": {"triage_model": "x", "complex_model": "y", "api_key": "k"}}
        warnings = validate_config(settings, sample_profiles_config)
        assert any("missing 'monitoring'" in w for w in warnings)

    def test_missing_safety_section(self, sample_profiles_config):
        settings = {
            "api": {"triage_model": "x", "complex_model": "y", "api_key": "k"},
            "monitoring": {"poll_interval_seconds": 5, "cpu_threshold_percent": 95, "ram_threshold_percent": 85},
        }
        warnings = validate_config(settings, sample_profiles_config)
        assert any("missing 'safety'" in w for w in warnings)

    def test_invalid_cost_cap(self, sample_settings, sample_profiles_config):
        settings = {**sample_settings}
        settings["api"] = {**settings["api"], "cost_cap_usd": -5}
        warnings = validate_config(settings, sample_profiles_config)
        assert any("cost_cap_usd" in w for w in warnings)

    def test_invalid_profile_name(self, sample_settings):
        profiles = {
            "profiles": {"invalid_profile": {"trigger_processes": []}},
            "global_whitelist": [],
        }
        warnings = validate_config(sample_settings, profiles)
        assert any("unknown profile" in w for w in warnings)

    def test_missing_profiles_section(self, sample_settings):
        warnings = validate_config(sample_settings, {})
        assert any("missing 'profiles'" in w for w in warnings)

    def test_non_list_trigger_processes(self, sample_settings):
        profiles = {
            "profiles": {"coding": {"trigger_processes": "not_a_list"}},
            "global_whitelist": [],
        }
        warnings = validate_config(sample_settings, profiles)
        assert any("must be a list" in w for w in warnings)

    def test_non_list_global_whitelist(self, sample_settings):
        profiles = {
            "profiles": {"idle": {"trigger_processes": [], "always_keep": [], "kill_candidates": [], "actions": {}}},
            "global_whitelist": "not_a_list",
        }
        warnings = validate_config(sample_settings, profiles)
        assert any("global_whitelist must be a list" in w for w in warnings)
