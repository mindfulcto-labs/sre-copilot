"""Load runbooks from YAML files and discover them on disk."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from sre_copilot.models import Runbook


class RunbookError(ValueError):
    """A runbook file could not be parsed or failed validation."""


def load_runbook(path: Path) -> Runbook:
    """Parse one YAML file into a validated Runbook."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RunbookError(f"{path}: cannot read file: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RunbookError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise RunbookError(f"{path}: expected a YAML mapping at the top level")
    try:
        return Runbook.model_validate(raw)
    except ValidationError as exc:
        raise RunbookError(f"{path}: {exc}") from exc


def default_dirs() -> list[Path]:
    """Directories searched for runbooks, in priority order.

    SRE_COPILOT_RUNBOOK_DIRS (colon-separated) overrides the default of
    ./runbooks followed by ./examples/runbooks.
    """
    env = os.environ.get("SRE_COPILOT_RUNBOOK_DIRS", "").strip()
    if env:
        return [Path(p) for p in env.split(":") if p]
    return [Path("runbooks"), Path("examples/runbooks")]


def discover_runbooks(dirs: list[Path] | None = None) -> dict[str, Path]:
    """Map runbook name to file path across the search directories.

    The first directory that defines a name wins, so a local ./runbooks
    directory can shadow the shipped examples.
    """
    found: dict[str, Path] = {}
    for directory in dirs if dirs is not None else default_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
            name = path.stem
            if name not in found:
                found[name] = path
    return found


def find_runbook(name: str, dirs: list[Path] | None = None) -> Runbook:
    """Find a runbook by name and load it, or raise RunbookError."""
    found = discover_runbooks(dirs)
    if name not in found:
        known = ", ".join(sorted(found)) or "none found"
        raise RunbookError(f"no runbook named '{name}' (known runbooks: {known})")
    runbook = load_runbook(found[name])
    if runbook.name != name:
        raise RunbookError(
            f"{found[name]}: file name '{name}' does not match runbook name '{runbook.name}'"
        )
    return runbook
