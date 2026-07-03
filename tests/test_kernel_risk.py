"""Tests for kernel risk levels and severity merging."""

from agentkernel.risk import RiskLevel, max_severity, parse_risk


def test_max_severity_takes_the_more_severe():
    assert max_severity(RiskLevel.LOW, RiskLevel.HIGH) is RiskLevel.HIGH
    assert max_severity(RiskLevel.HIGH, RiskLevel.LOW) is RiskLevel.HIGH
    assert max_severity(RiskLevel.MEDIUM, RiskLevel.LOW) is RiskLevel.MEDIUM
    assert max_severity(RiskLevel.MEDIUM, RiskLevel.MEDIUM) is RiskLevel.MEDIUM


def test_parse_risk_accepts_canonical_strings():
    assert parse_risk("low") is RiskLevel.LOW
    assert parse_risk("medium") is RiskLevel.MEDIUM
    assert parse_risk("high") is RiskLevel.HIGH


def test_parse_risk_falls_back_on_garbage():
    assert parse_risk("bogus") is RiskLevel.MEDIUM
    assert parse_risk("") is RiskLevel.MEDIUM
    assert parse_risk("bogus", default=RiskLevel.HIGH) is RiskLevel.HIGH
