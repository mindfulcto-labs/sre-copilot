"""Step-by-step runbook execution with an approval gate and audit logging.

Rules of the road:
- Mutating steps never run without approval. The --yes flag (auto_approve)
  or an interactive confirmation is required.
- Dry-run mode executes nothing and prompts for nothing. Every step is
  recorded as skipped with a description of what would have run.
- The first failed or declined step stops the run.
- Every step outcome, including dry-run and declined steps, is written
  to the audit log.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import time
from collections.abc import Callable

import httpx

from sre_copilot.audit import AuditLog
from sre_copilot.models import (
    Alert,
    AuditRecord,
    Runbook,
    RunbookStep,
    StepResult,
    StepStatus,
    StepType,
)

_DETAIL_LIMIT = 500


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= _DETAIL_LIMIT:
        return text
    return text[:_DETAIL_LIMIT] + " ...[truncated]"


def _default_approver() -> str:
    env = os.environ.get("SRE_COPILOT_APPROVER", "").strip()
    if env:
        return env
    try:
        return getpass.getuser()
    except OSError:
        return "unknown"


class RunbookExecutor:
    """Runs a runbook one step at a time."""

    def __init__(
        self,
        audit_log: AuditLog,
        *,
        dry_run: bool = False,
        auto_approve: bool = False,
        approver: str | None = None,
        confirm: Callable[[str], bool] | None = None,
        command_timeout: float = 120.0,
        on_step: Callable[[RunbookStep, StepResult], None] | None = None,
    ) -> None:
        self.audit_log = audit_log
        self.dry_run = dry_run
        self.auto_approve = auto_approve
        self.approver = approver or _default_approver()
        self.confirm = confirm if confirm is not None else self._prompt
        self.command_timeout = command_timeout
        self.on_step = on_step

    @staticmethod
    def _prompt(question: str) -> bool:
        answer = input(f"{question} [y/N]: ").strip().lower()
        return answer in ("y", "yes")

    def run(self, runbook: Runbook, alert: Alert | None = None) -> list[StepResult]:
        """Execute the runbook and return one result per attempted step."""
        env = os.environ.copy()
        if alert is not None:
            env.update(
                {
                    "SRE_ALERT_SOURCE": alert.source,
                    "SRE_ALERT_SEVERITY": alert.severity.value,
                    "SRE_ALERT_SERVICE": alert.service,
                    "SRE_ALERT_MESSAGE": alert.message,
                }
            )
        results: list[StepResult] = []
        for step in runbook.steps:
            result = self._run_step(step, env)
            results.append(result)
            self._record(runbook, step, result)
            if self.on_step is not None:
                self.on_step(step, result)
            if result.status in (StepStatus.FAILED, StepStatus.DECLINED):
                break
        return results

    def _run_step(self, step: RunbookStep, env: dict[str, str]) -> StepResult:
        if self.dry_run:
            return StepResult(
                step=step.name,
                status=StepStatus.SKIPPED,
                detail=f"dry run: would {self._describe(step)}",
            )
        if step.mutating and not self.auto_approve:
            question = f"Step '{step.name}' makes changes ({step.command}). Approve?"
            if not self.confirm(question):
                return StepResult(
                    step=step.name,
                    status=StepStatus.DECLINED,
                    detail="approval declined, run stopped",
                )
        started = time.monotonic()
        try:
            status, detail = self._execute(step, env)
        except Exception as exc:  # noqa: BLE001 - one step must not crash the run
            status, detail = StepStatus.FAILED, f"unexpected error: {exc}"
        return StepResult(
            step=step.name,
            status=status,
            detail=_truncate(detail),
            duration_seconds=round(time.monotonic() - started, 3),
        )

    @staticmethod
    def _describe(step: RunbookStep) -> str:
        if step.type in (StepType.COMMAND, StepType.VERIFY):
            prefix = "run" if step.type is StepType.COMMAND else "verify with"
            gate = " (mutating, needs approval)" if step.mutating else ""
            return f"{prefix}: {step.command}{gate}"
        if step.type is StepType.HTTP_CHECK:
            return f"check GET {step.url} expects status {step.expected_status}"
        return f"wait {step.seconds} seconds"

    def _execute(self, step: RunbookStep, env: dict[str, str]) -> tuple[StepStatus, str]:
        if step.type in (StepType.COMMAND, StepType.VERIFY):
            return self._execute_command(step, env)
        if step.type is StepType.HTTP_CHECK:
            return self._execute_http_check(step)
        assert step.seconds is not None  # validated at model level
        time.sleep(step.seconds)
        return StepStatus.OK, f"waited {step.seconds} seconds"

    def _execute_command(self, step: RunbookStep, env: dict[str, str]) -> tuple[StepStatus, str]:
        assert step.command is not None  # validated at model level
        try:
            completed = subprocess.run(
                step.command,
                shell=True,
                capture_output=True,
                text=True,
                env=env,
                timeout=self.command_timeout,
            )
        except subprocess.TimeoutExpired:
            return StepStatus.FAILED, f"timed out after {self.command_timeout} seconds"
        output = completed.stdout or completed.stderr
        if completed.returncode == 0:
            return StepStatus.OK, output
        return (
            StepStatus.FAILED,
            f"exit code {completed.returncode}: {output or 'no output'}",
        )

    def _execute_http_check(self, step: RunbookStep) -> tuple[StepStatus, str]:
        assert step.url is not None  # validated at model level
        try:
            response = httpx.get(step.url, timeout=10.0, follow_redirects=True)
        except httpx.HTTPError as exc:
            return StepStatus.FAILED, f"request failed: {exc}"
        if response.status_code == step.expected_status:
            return StepStatus.OK, f"GET {step.url} returned {response.status_code}"
        return (
            StepStatus.FAILED,
            f"GET {step.url} returned {response.status_code}, expected {step.expected_status}",
        )

    def _record(self, runbook: Runbook, step: RunbookStep, result: StepResult) -> None:
        approved_by = None
        if step.mutating and result.status == StepStatus.OK and not self.dry_run:
            approved_by = self.approver
        self.audit_log.append(
            AuditRecord.now(
                runbook=runbook.name,
                step=step.name,
                step_type=step.type,
                status=result.status,
                detail=result.detail,
                approved_by=approved_by,
                dry_run=self.dry_run,
            )
        )
