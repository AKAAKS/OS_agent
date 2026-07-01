"""
Pydantic schemas for OS Agent configuration files.

Validates `config/settings.yaml` and `config/profiles.yaml` at startup and
fails fast with a human-readable error listing every bad field. Unlike the
previous manual checks, this catches typos, wrong types, and out-of-range
values before any subsystem is initialized.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


VALID_PROFILE_NAMES = {
    "idle", "coding", "gaming", "streaming", "browsing", "media", "comms",
}

VALID_RISK_LEVELS = {"low", "medium", "high"}


class ConfigValidationError(Exception):
    """Raised when configuration fails schema validation."""


# ---------- settings.yaml schema ----------


class APIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai"] = "openai"
    api_key: str = ""
    triage_model: str = Field(..., min_length=1)
    complex_model: str = Field(..., min_length=1)
    confidence_threshold: float = Field(0.7, ge=0.0, le=1.0)
    cost_cap_usd: float = Field(1.0, gt=0.0)
    daily_cost_cap_usd: float = Field(5.0, gt=0.0)

    @field_validator("api_key")
    @classmethod
    def resolve_env(cls, v: str) -> str:
        """Expand ${OPENAI_API_KEY} from environment."""
        if v and v.startswith("${") and v.endswith("}"):
            env_name = v[2:-1]
            return os.environ.get(env_name, "")
        return v


class MonitoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval_seconds: int = Field(5, ge=1, le=300)
    poll_interval_battery_seconds: int = Field(30, ge=1, le=600)
    cpu_threshold_percent: float = Field(95.0, gt=0.0, le=100.0)
    cpu_sustained_seconds: int = Field(120, ge=1)
    ram_threshold_percent: float = Field(85.0, gt=0.0, le=100.0)
    disk_threshold_percent: float = Field(90.0, gt=0.0, le=100.0)
    memory_leak_growth_mb: float = Field(500.0, gt=0.0)
    memory_leak_window_minutes: int = Field(30, ge=1)
    gpu_threshold_percent: float = Field(95.0, gt=0.0, le=100.0)
    cooldown_minutes: int = Field(10, ge=0)
    pause_on_battery: bool = False


class DecisionsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_learn_after_n: int = Field(3, ge=1)
    similarity_threshold: float = Field(0.92, ge=0.0, le=1.0)
    debounce_seconds: int = Field(30, ge=0)


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high_risk_actions: list[str] = Field(default_factory=list)
    auto_approve_risk_levels: list[str] = Field(default_factory=lambda: ["low"])

    @field_validator("auto_approve_risk_levels")
    @classmethod
    def check_risk_levels(cls, v: list[str]) -> list[str]:
        bad = [r for r in v if r not in VALID_RISK_LEVELS]
        if bad:
            raise ValueError(
                f"invalid risk levels {bad} — must be one of {sorted(VALID_RISK_LEVELS)}"
            )
        return v


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    file: str = "logs/agent.log"
    max_bytes: int = Field(10 * 1024 * 1024, gt=0)
    backup_count: int = Field(3, ge=0)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api: APIConfig
    monitoring: MonitoringConfig
    decisions: DecisionsConfig = Field(default_factory=DecisionsConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def warn_missing_api_key(self) -> "AppConfig":
        if not self.api.api_key and not os.environ.get("OPENAI_API_KEY"):
            # not fatal — LLM calls will fail gracefully, but worth warning about
            import logging
            logging.getLogger(__name__).warning(
                "No OPENAI_API_KEY configured; LLM calls will be disabled."
            )
        return self


# ---------- profiles.yaml schema ----------


class ProfileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_processes: list[str] = Field(default_factory=list)
    always_keep: list[str] = Field(default_factory=list)
    kill_candidates: list[str] = Field(default_factory=list)
    actions: dict[str, Any] = Field(default_factory=dict)


class ProfilesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: dict[str, ProfileEntry]
    global_whitelist: list[str] = Field(default_factory=list)
    global_blacklist: list[str] = Field(default_factory=list)

    @field_validator("profiles")
    @classmethod
    def check_profile_names(cls, v: dict[str, ProfileEntry]) -> dict[str, ProfileEntry]:
        unknown = [name for name in v if name not in VALID_PROFILE_NAMES]
        if unknown:
            raise ValueError(
                f"unknown profile names {unknown} — must be one of {sorted(VALID_PROFILE_NAMES)}"
            )
        return v


# ---------- public loader ----------


def _format_errors(exc: ValidationError, source: str) -> str:
    lines = [f"Configuration error in {source}:"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        msg = err["msg"]
        lines.append(f"  - {loc}: {msg}")
    return "\n".join(lines)


def load_and_validate(
    settings_path: Path | str = "config/settings.yaml",
    profiles_path: Path | str = "config/profiles.yaml",
) -> tuple[AppConfig, ProfilesConfig]:
    """Load and schema-validate settings + profiles. Raises ConfigValidationError on failure."""
    settings_path = Path(settings_path)
    profiles_path = Path(profiles_path)

    if not settings_path.exists():
        raise ConfigValidationError(f"Config file not found: {settings_path.resolve()}")
    if not profiles_path.exists():
        raise ConfigValidationError(f"Config file not found: {profiles_path.resolve()}")

    try:
        with open(settings_path, encoding="utf-8") as f:
            settings_raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigValidationError(f"YAML parse error in {settings_path}: {e}") from e

    try:
        with open(profiles_path, encoding="utf-8") as f:
            profiles_raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigValidationError(f"YAML parse error in {profiles_path}: {e}") from e

    try:
        app_config = AppConfig.model_validate(settings_raw)
    except ValidationError as e:
        raise ConfigValidationError(_format_errors(e, str(settings_path))) from e

    try:
        profiles_config = ProfilesConfig.model_validate(profiles_raw)
    except ValidationError as e:
        raise ConfigValidationError(_format_errors(e, str(profiles_path))) from e

    return app_config, profiles_config
