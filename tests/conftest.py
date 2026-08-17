"""Shared fixtures. The whole suite runs offline with no API keys."""

from __future__ import annotations

import pytest

from sre_copilot.audit import AuditLog
from sre_copilot.models import Alert, Severity


@pytest.fixture(autouse=True)
def _no_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic even when the host has API keys set."""
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "SRE_COPILOT_PROVIDER",
        "SRE_COPILOT_MODEL",
        "SRE_COPILOT_RUNBOOK_DIRS",
        "SRE_COPILOT_AUDIT_DIR",
        "SRE_COPILOT_APPROVER",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def disk_alert() -> Alert:
    return Alert(
        source="prometheus",
        severity=Severity.CRITICAL,
        service="payments-api",
        message="Filesystem /var is 96% full, disk pressure imminent",
        labels={"env": "prod", "host": "payments-api-3"},
    )


@pytest.fixture
def audit_log(tmp_path) -> AuditLog:
    return AuditLog(tmp_path / "audit")
