"""Typed data models for alerts, triage results, runbooks and audit records."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Severity(StrEnum):
    """Alert severity as reported by the alert source."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Alert(BaseModel):
    """An alert ingested from a monitoring system.

    The JSON shape is deliberately small so that most alert sources
    (Prometheus Alertmanager, Grafana, CloudWatch, a hand-written file)
    can be mapped onto it with a few lines of glue.
    """

    model_config = ConfigDict(extra="ignore")

    source: str = Field(min_length=1, description="Where the alert came from.")
    severity: Severity
    service: str = Field(min_length=1, description="The service the alert is about.")
    message: str = Field(min_length=1, description="Human-readable alert text.")
    labels: dict[str, str] = Field(default_factory=dict)


class Category(StrEnum):
    """Coarse incident category assigned by triage."""

    DISK = "disk"
    AVAILABILITY = "availability"
    CERTIFICATE = "certificate"
    MEMORY = "memory"
    LATENCY = "latency"
    NETWORK = "network"
    UNKNOWN = "unknown"


class Urgency(StrEnum):
    """How quickly a human should look at the alert."""

    IMMEDIATE = "immediate"
    HIGH = "high"
    ROUTINE = "routine"


class TriageResult(BaseModel):
    """The outcome of triaging one alert."""

    category: Category
    likely_cause: str
    urgency: Urgency
    recommended_runbook: str | None = None
    engine: str = Field(description="Which engine produced the result, e.g. 'rules' or 'openai'.")
    confidence: float = Field(ge=0.0, le=1.0)


class StepType(StrEnum):
    """The kinds of step a runbook may contain."""

    COMMAND = "command"
    HTTP_CHECK = "http_check"
    WAIT = "wait"
    VERIFY = "verify"


class RunbookStep(BaseModel):
    """One step in a runbook.

    Field requirements depend on the step type:
    - command and verify steps need ``command``
    - http_check steps need ``url``
    - wait steps need ``seconds``
    """

    name: str = Field(min_length=1)
    type: StepType
    command: str | None = None
    url: str | None = None
    expected_status: int = Field(default=200, ge=100, le=599)
    seconds: float | None = Field(default=None, gt=0)
    mutating: bool = False

    @model_validator(mode="after")
    def _check_fields_for_type(self) -> RunbookStep:
        if self.type in (StepType.COMMAND, StepType.VERIFY) and not self.command:
            raise ValueError(f"step '{self.name}': {self.type.value} steps need a 'command' field")
        if self.type is StepType.HTTP_CHECK and not self.url:
            raise ValueError(f"step '{self.name}': http_check steps need a 'url' field")
        if self.type is StepType.WAIT and self.seconds is None:
            raise ValueError(f"step '{self.name}': wait steps need a 'seconds' field")
        if self.type in (StepType.WAIT, StepType.HTTP_CHECK, StepType.VERIFY) and self.mutating:
            raise ValueError(
                f"step '{self.name}': only command steps may be marked as mutating"
            )
        return self


class Runbook(BaseModel):
    """A named sequence of steps loaded from a YAML file."""

    name: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str = ""
    category: Category | None = None
    steps: list[RunbookStep] = Field(min_length=1)

    @field_validator("steps")
    @classmethod
    def _unique_step_names(cls, steps: list[RunbookStep]) -> list[RunbookStep]:
        names = [s.name for s in steps]
        if len(names) != len(set(names)):
            raise ValueError("step names within a runbook must be unique")
        return steps


class StepStatus(StrEnum):
    """Result state of one executed step."""

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    DECLINED = "declined"


class StepResult(BaseModel):
    """What happened when one step ran (or was skipped)."""

    step: str
    status: StepStatus
    detail: str = ""
    duration_seconds: float = 0.0


class AuditRecord(BaseModel):
    """One line in the JSON-lines audit log."""

    timestamp: datetime
    runbook: str
    step: str
    step_type: StepType
    status: StepStatus
    detail: str = ""
    approved_by: str | None = None
    dry_run: bool = False

    @classmethod
    def now(cls, **kwargs: object) -> AuditRecord:
        """Build a record stamped with the current UTC time."""
        return cls(timestamp=datetime.now(UTC), **kwargs)  # type: ignore[arg-type]
