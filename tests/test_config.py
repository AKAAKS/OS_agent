"""Tests for config schema validation (Pydantic-based)."""

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import (
    AppConfig,
    ConfigValidationError,
    ProfilesConfig,
    load_and_validate,
)


def _validate_settings(raw: dict) -> list[str]:
    """Return list of validation error messages for a settings dict, or [] if valid."""
    try:
        AppConfig.model_validate(raw)
        return []
    except Exception as e:
        return [str(e)]


def _validate_profiles(raw: dict) -> list[str]:
    try:
        ProfilesConfig.model_validate(raw)
        return []
    except Exception as e:
        return [str(e)]


class TestSettingsSchema:

    def test_valid_config_passes(self, sample_settings):
        errs = _validate_settings(sample_settings)
        assert errs == []

    def test_missing_api_section_fails(self):
        errs = _validate_settings({"monitoring": {}})
        assert any("api" in e for e in errs)

    def test_missing_monitoring_section_fails(self):
        errs = _validate_settings({"api": {"triage_model": "x", "complex_model": "y"}})
        assert any("monitoring" in e for e in errs)

    def test_negative_cost_cap_fails(self, sample_settings):
        bad = {**sample_settings, "api": {**sample_settings["api"], "cost_cap_usd": -5}}
        errs = _validate_settings(bad)
        assert any("cost_cap_usd" in e for e in errs)

    def test_out_of_range_cpu_threshold_fails(self, sample_settings):
        bad = {**sample_settings, "monitoring": {**sample_settings["monitoring"], "cpu_threshold_percent": 150}}
        errs = _validate_settings(bad)
        assert any("cpu_threshold_percent" in e for e in errs)

    def test_extra_unknown_key_fails(self, sample_settings):
        bad = {**sample_settings, "api": {**sample_settings["api"], "unknown_field": "x"}}
        errs = _validate_settings(bad)
        assert any("unknown_field" in e.lower() or "extra" in e.lower() for e in errs)

    def test_invalid_risk_level_fails(self, sample_settings):
        bad = {**sample_settings, "safety": {"auto_approve_risk_levels": ["extreme"]}}
        errs = _validate_settings(bad)
        assert any("risk" in e.lower() for e in errs)

    def test_api_key_env_expansion(self, sample_settings, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-value")
        cfg = AppConfig.model_validate({**sample_settings, "api": {**sample_settings["api"], "api_key": "${OPENAI_API_KEY}"}})
        assert cfg.api.api_key == "sk-test-value"


class TestProfilesSchema:

    def test_valid_profiles_pass(self, sample_profiles_config):
        errs = _validate_profiles(sample_profiles_config)
        assert errs == []

    def test_unknown_profile_name_fails(self):
        raw = {"profiles": {"invalid_profile": {"trigger_processes": []}}}
        errs = _validate_profiles(raw)
        assert any("invalid_profile" in e or "unknown" in e.lower() for e in errs)

    def test_missing_profiles_section_fails(self):
        errs = _validate_profiles({})
        assert any("profiles" in e for e in errs)

    def test_non_list_trigger_processes_fails(self):
        raw = {"profiles": {"coding": {"trigger_processes": "not_a_list"}}}
        errs = _validate_profiles(raw)
        assert errs  # anything non-empty is a pass here

    def test_non_list_global_whitelist_fails(self):
        raw = {
            "profiles": {"idle": {}},
            "global_whitelist": "not_a_list",
        }
        errs = _validate_profiles(raw)
        assert errs


class TestLoadAndValidate:

    def test_roundtrip_real_config_files(self, tmp_path, sample_settings, sample_profiles_config):
        s = tmp_path / "settings.yaml"
        p = tmp_path / "profiles.yaml"
        s.write_text(yaml.safe_dump(sample_settings))
        p.write_text(yaml.safe_dump(sample_profiles_config))

        app, profiles = load_and_validate(s, p)
        assert app.api.triage_model == sample_settings["api"]["triage_model"]
        assert set(profiles.profiles.keys()) == set(sample_profiles_config["profiles"].keys())

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigValidationError, match="not found"):
            load_and_validate(tmp_path / "nope.yaml", tmp_path / "also-nope.yaml")

    def test_bad_yaml_raises(self, tmp_path, sample_profiles_config):
        s = tmp_path / "settings.yaml"
        p = tmp_path / "profiles.yaml"
        s.write_text("api: : invalid: yaml:\n  - ]")
        p.write_text(yaml.safe_dump(sample_profiles_config))

        with pytest.raises(ConfigValidationError):
            load_and_validate(s, p)
