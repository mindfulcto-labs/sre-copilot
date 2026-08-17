"""Model validation behaviour."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sre_copilot.models import (
    Alert,
    Runbook,
    RunbookStep,
    StepType,
    TriageResult,
)


def test_alert_parses_minimal_json() -> None:
    alert = Alert.model_validate(
        {
            "source": "prometheus",
            "severity": "warning",
            "service": "api",
            "message": "something happened",
        }
    )
    assert alert.labels == {}
    assert alert.severity.value == "warning"


def test_alert_rejects_unknown_severity() -> None:
    with pytest.raises(ValidationError):
        Alert.model_validate(
            {"source": "x", "severity": "catastrophic", "service": "api", "message": "boom"}
        )


def test_alert_ignores_extra_fields() -> None:
    alert = Alert.model_validate(
        {
            "source": "grafana",
            "severity": "info",
            "service": "api",
            "message": "hello",
            "fingerprint": "abc123",
        }
    )
    assert not hasattr(alert, "fingerprint")


def test_command_step_requires_command() -> None:
    with pytest.raises(ValidationError, match="need a 'command'"):
        RunbookStep(name="broken", type=StepType.COMMAND)


def test_http_check_step_requires_url() -> None:
    with pytest.raises(ValidationError, match="need a 'url'"):
        RunbookStep(name="broken", type=StepType.HTTP_CHECK)


def test_wait_step_requires_positive_seconds() -> None:
    with pytest.raises(ValidationError):
        RunbookStep(name="broken", type=StepType.WAIT, seconds=-1)


def test_only_command_steps_may_be_mutating() -> None:
    with pytest.raises(ValidationError, match="mutating"):
        RunbookStep(name="broken", type=StepType.WAIT, seconds=1, mutating=True)


def test_runbook_requires_at_least_one_step() -> None:
    with pytest.raises(ValidationError):
        Runbook(name="empty", steps=[])


def test_runbook_rejects_duplicate_step_names() -> None:
    step = {"name": "same", "type": "command", "command": "true"}
    with pytest.raises(ValidationError, match="unique"):
        Runbook.model_validate({"name": "dupes", "steps": [step, step]})


def test_triage_result_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        TriageResult(
            category="disk",
            likely_cause="x",
            urgency="high",
            engine="rules",
            confidence=1.5,
        )
