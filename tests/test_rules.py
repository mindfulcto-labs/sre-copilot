"""Deterministic rule engine behaviour."""

from __future__ import annotations

import pytest

from sre_copilot.models import Alert, Category, Severity, Urgency
from sre_copilot.triage.rules import classify


def _alert(message: str, severity: Severity = Severity.WARNING, **labels: str) -> Alert:
    return Alert(
        source="test",
        severity=severity,
        service="some-service",
        message=message,
        labels=labels,
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Filesystem /var is 96% full, disk pressure", Category.DISK),
        ("No space left on device", Category.DISK),
        ("TLS certificate expires in 6 days", Category.CERTIFICATE),
        ("Service is down, connection refused", Category.AVAILABILITY),
        ("Pod stuck in CrashLoopBackOff", Category.AVAILABILITY),
        ("OOMKilled: memory limit exceeded", Category.MEMORY),
        ("p99 latency above 2s", Category.LATENCY),
        ("DNS resolution failing, packet loss observed", Category.NETWORK),
    ],
)
def test_classification_categories(message: str, expected: Category) -> None:
    assert classify(_alert(message)).category == expected


def test_unmatched_alert_is_unknown_with_low_confidence() -> None:
    result = classify(_alert("Nightly report generated"))
    assert result.category == Category.UNKNOWN
    assert result.recommended_runbook is None
    assert result.confidence < 0.5


def test_recommended_runbooks() -> None:
    assert classify(_alert("disk almost full")).recommended_runbook == "disk-pressure"
    assert classify(_alert("certificate expired")).recommended_runbook == "certificate-expiry"
    assert classify(_alert("service down")).recommended_runbook == "service-restart"


def test_urgency_follows_severity() -> None:
    assert classify(_alert("disk full", Severity.CRITICAL)).urgency == Urgency.IMMEDIATE
    assert classify(_alert("disk full", Severity.WARNING)).urgency == Urgency.HIGH
    assert classify(_alert("disk full", Severity.INFO)).urgency == Urgency.ROUTINE


def test_matched_prod_alert_never_routine() -> None:
    result = classify(_alert("disk full", Severity.INFO, env="prod"))
    assert result.urgency == Urgency.HIGH


def test_unmatched_prod_info_alert_stays_routine() -> None:
    result = classify(_alert("nothing to see here", Severity.INFO, env="prod"))
    assert result.urgency == Urgency.ROUTINE


def test_labels_contribute_to_classification() -> None:
    result = classify(
        _alert("threshold breached", alertname="NodeFilesystemAlmostFull", mountpoint="/var")
    )
    assert result.category == Category.DISK


def test_classification_is_deterministic(disk_alert: Alert) -> None:
    first = classify(disk_alert)
    second = classify(disk_alert)
    assert first == second
    assert first.engine == "rules"


def test_more_keyword_hits_raise_confidence() -> None:
    one_hit = classify(_alert("disk trouble"))
    two_hits = classify(_alert("disk trouble, filesystem filling"))
    assert two_hits.confidence > one_hit.confidence
