"""JSON-lines audit log for runbook executions.

Every executed, skipped or declined step becomes one line in
audit/audit.jsonl with a UTC timestamp, the step outcome and who
approved any mutating step. The log is append-only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from sre_copilot.models import AuditRecord

DEFAULT_DIR = Path("audit")
LOG_FILENAME = "audit.jsonl"


def audit_dir() -> Path:
    """The audit directory, overridable with SRE_COPILOT_AUDIT_DIR."""
    env = os.environ.get("SRE_COPILOT_AUDIT_DIR", "").strip()
    return Path(env) if env else DEFAULT_DIR


class AuditLog:
    """Append and read audit records under one directory."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory if directory is not None else audit_dir()

    @property
    def path(self) -> Path:
        return self.directory / LOG_FILENAME

    def append(self, record: AuditRecord) -> None:
        """Write one record as a JSON line, creating the directory if needed."""
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")

    def read(self) -> list[AuditRecord]:
        """Read all records in write order. Corrupt lines raise ValueError."""
        if not self.path.is_file():
            return []
        records: list[AuditRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(AuditRecord.model_validate(json.loads(line)))
                except (json.JSONDecodeError, ValidationError) as exc:
                    message = f"{self.path}:{line_number}: corrupt audit line: {exc}"
                    raise ValueError(message) from exc
        return records
