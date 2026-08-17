"""Executor behaviour: dry-run, approval gate, failure handling, env wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from sre_copilot.audit import AuditLog
from sre_copilot.models import Alert, Runbook, StepStatus
from sre_copilot.runbooks.executor import RunbookExecutor


def _runbook(*steps: dict) -> Runbook:
    return Runbook.model_validate({"name": "test-book", "steps": list(steps)})


def test_dry_run_executes_nothing_and_never_prompts(
    audit_log: AuditLog, tmp_path: Path
) -> None:
    marker = tmp_path / "marker.txt"
    runbook = _runbook(
        {
            "name": "create-marker",
            "type": "command",
            "command": f"touch {marker}",
            "mutating": True,
        },
        {"name": "check", "type": "verify", "command": "true"},
    )

    def explode(question: str) -> bool:
        raise AssertionError("dry run must not prompt for approval")

    executor = RunbookExecutor(audit_log, dry_run=True, confirm=explode)
    results = executor.run(runbook)
    assert not marker.exists()
    assert [r.status for r in results] == [StepStatus.SKIPPED, StepStatus.SKIPPED]
    assert all(r.detail.startswith("dry run:") for r in results)


def test_mutating_step_runs_after_approval(audit_log: AuditLog, tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    runbook = _runbook(
        {
            "name": "create-marker",
            "type": "command",
            "command": f"touch {marker}",
            "mutating": True,
        },
    )
    executor = RunbookExecutor(audit_log, confirm=lambda q: True, approver="test-user")
    results = executor.run(runbook)
    assert marker.exists()
    assert results[0].status == StepStatus.OK


def test_declined_approval_stops_the_run(audit_log: AuditLog, tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    runbook = _runbook(
        {"name": "gate", "type": "command", "command": "true", "mutating": True},
        {"name": "after", "type": "command", "command": f"touch {marker}"},
    )
    executor = RunbookExecutor(audit_log, confirm=lambda q: False)
    results = executor.run(runbook)
    assert [r.status for r in results] == [StepStatus.DECLINED]
    assert not marker.exists()


def test_auto_approve_skips_the_prompt(audit_log: AuditLog) -> None:
    runbook = _runbook({"name": "gate", "type": "command", "command": "true", "mutating": True})

    def explode(question: str) -> bool:
        raise AssertionError("auto-approve must not prompt")

    executor = RunbookExecutor(audit_log, auto_approve=True, confirm=explode)
    results = executor.run(runbook)
    assert results[0].status == StepStatus.OK


def test_non_mutating_steps_never_prompt(audit_log: AuditLog) -> None:
    runbook = _runbook({"name": "look", "type": "command", "command": "echo observing"})

    def explode(question: str) -> bool:
        raise AssertionError("read-only steps must not prompt")

    executor = RunbookExecutor(audit_log, confirm=explode)
    results = executor.run(runbook)
    assert results[0].status == StepStatus.OK
    assert "observing" in results[0].detail


def test_failed_step_stops_the_run(audit_log: AuditLog) -> None:
    runbook = _runbook(
        {"name": "fails", "type": "verify", "command": "false"},
        {"name": "never-runs", "type": "command", "command": "echo unreachable"},
    )
    executor = RunbookExecutor(audit_log, confirm=lambda q: True)
    results = executor.run(runbook)
    assert [r.status for r in results] == [StepStatus.FAILED]
    assert "exit code 1" in results[0].detail


def test_alert_context_reaches_commands(audit_log: AuditLog, disk_alert: Alert) -> None:
    runbook = _runbook(
        {"name": "echo-service", "type": "command", "command": "echo service=$SRE_ALERT_SERVICE"},
    )
    executor = RunbookExecutor(audit_log, confirm=lambda q: True)
    results = executor.run(runbook, disk_alert)
    assert "service=payments-api" in results[0].detail


def test_wait_step_runs(audit_log: AuditLog) -> None:
    runbook = _runbook({"name": "pause", "type": "wait", "seconds": 0.05})
    executor = RunbookExecutor(audit_log, confirm=lambda q: True)
    results = executor.run(runbook)
    assert results[0].status == StepStatus.OK


def test_http_check_compares_status(
    audit_log: AuditLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeResponse:
        status_code = 200

    monkeypatch.setattr(
        "sre_copilot.runbooks.executor.httpx.get",
        lambda url, timeout, follow_redirects: FakeResponse(),
    )
    ok_book = _runbook(
        {"name": "health", "type": "http_check", "url": "http://x/healthz", "expected_status": 200}
    )
    bad_book = _runbook(
        {"name": "health", "type": "http_check", "url": "http://x/healthz", "expected_status": 503}
    )
    executor = RunbookExecutor(audit_log, confirm=lambda q: True)
    assert executor.run(ok_book)[0].status == StepStatus.OK
    assert executor.run(bad_book)[0].status == StepStatus.FAILED


def test_command_timeout_fails_the_step(audit_log: AuditLog) -> None:
    runbook = _runbook({"name": "slow", "type": "command", "command": "sleep 5"})
    executor = RunbookExecutor(audit_log, confirm=lambda q: True, command_timeout=0.2)
    results = executor.run(runbook)
    assert results[0].status == StepStatus.FAILED
    assert "timed out" in results[0].detail
