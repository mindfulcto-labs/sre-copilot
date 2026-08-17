"""Audit log writing and reading."""

from __future__ import annotations

from pathlib import Path

import pytest

from sre_copilot.audit import AuditLog
from sre_copilot.models import AuditRecord, Runbook, StepStatus, StepType
from sre_copilot.runbooks.executor import RunbookExecutor


def test_append_and_read_roundtrip(audit_log: AuditLog) -> None:
    record = AuditRecord.now(
        runbook="disk-pressure",
        step="show-disk-usage",
        step_type=StepType.COMMAND,
        status=StepStatus.OK,
        detail="ok",
        approved_by=None,
        dry_run=False,
    )
    audit_log.append(record)
    read_back = audit_log.read()
    assert len(read_back) == 1
    assert read_back[0] == record


def test_read_empty_log_returns_empty_list(audit_log: AuditLog) -> None:
    assert audit_log.read() == []


def test_corrupt_line_raises_with_location(audit_log: AuditLog) -> None:
    audit_log.directory.mkdir(parents=True)
    audit_log.path.write_text("this is not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt audit line"):
        audit_log.read()


def test_executor_audits_every_step_with_approver(
    audit_log: AuditLog, tmp_path: Path
) -> None:
    runbook = Runbook.model_validate(
        {
            "name": "audited",
            "steps": [
                {"name": "observe", "type": "command", "command": "echo hi"},
                {"name": "change", "type": "command", "command": "true", "mutating": True},
            ],
        }
    )
    executor = RunbookExecutor(audit_log, confirm=lambda q: True, approver="venkat")
    executor.run(runbook)
    records = audit_log.read()
    assert [r.step for r in records] == ["observe", "change"]
    assert records[0].approved_by is None
    assert records[1].approved_by == "venkat"
    assert all(not r.dry_run for r in records)


def test_dry_run_steps_are_audited_as_dry_run(audit_log: AuditLog) -> None:
    runbook = Runbook.model_validate(
        {
            "name": "audited",
            "steps": [{"name": "change", "type": "command", "command": "true", "mutating": True}],
        }
    )
    executor = RunbookExecutor(audit_log, dry_run=True)
    executor.run(runbook)
    records = audit_log.read()
    assert len(records) == 1
    assert records[0].dry_run is True
    assert records[0].approved_by is None
    assert records[0].status == StepStatus.SKIPPED
