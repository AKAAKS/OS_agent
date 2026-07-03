"""Risk levels and severity ordering for autonomous actions."""

from enum import Enum


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def max_severity(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    """The more severe of two risk levels.

    Used to merge an upstream risk assessment with an action's canonical
    risk so that neither a mislabelled upstream value nor a stale canonical
    entry can downgrade the other.
    """
    return a if _ORDER[a] >= _ORDER[b] else b


def parse_risk(value: str, default: RiskLevel = RiskLevel.MEDIUM) -> RiskLevel:
    """Parse a risk string, falling back to `default` on anything invalid."""
    try:
        return RiskLevel(value)
    except ValueError:
        return default
