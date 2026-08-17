"""Runbook YAML parsing and discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from sre_copilot.models import StepType
from sre_copilot.runbooks import RunbookError, discover_runbooks, find_runbook, load_runbook

VALID = """
name: sample
description: A sample runbook.
steps:
  - name: say-hello
    type: command
    command: echo hello
  - name: pause
    type: wait
    seconds: 0.1
"""


def _write(directory: Path, filename: str, content: str) -> Path:
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


def test_load_valid_runbook(tmp_path: Path) -> None:
    runbook = load_runbook(_write(tmp_path, "sample.yaml", VALID))
    assert runbook.name == "sample"
    assert [s.type for s in runbook.steps] == [StepType.COMMAND, StepType.WAIT]


def test_load_rejects_invalid_yaml(tmp_path: Path) -> None:
    with pytest.raises(RunbookError, match="invalid YAML"):
        load_runbook(_write(tmp_path, "bad.yaml", "steps: [unclosed"))


def test_load_rejects_non_mapping(tmp_path: Path) -> None:
    with pytest.raises(RunbookError, match="mapping"):
        load_runbook(_write(tmp_path, "list.yaml", "- just\n- a\n- list\n"))


def test_load_rejects_step_missing_command(tmp_path: Path) -> None:
    content = "name: broken\nsteps:\n  - name: no-command\n    type: command\n"
    with pytest.raises(RunbookError, match="need a 'command'"):
        load_runbook(_write(tmp_path, "broken.yaml", content))


def test_discover_first_directory_shadows_second(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write(first, "sample.yaml", VALID)
    _write(second, "sample.yaml", VALID)
    _write(second, "other.yaml", VALID.replace("name: sample", "name: other"))
    found = discover_runbooks([first, second])
    assert found["sample"].parent == first
    assert set(found) == {"sample", "other"}


def test_find_runbook_unknown_name_lists_known(tmp_path: Path) -> None:
    _write(tmp_path, "sample.yaml", VALID)
    with pytest.raises(RunbookError, match="known runbooks: sample"):
        find_runbook("missing", [tmp_path])


def test_find_runbook_name_must_match_filename(tmp_path: Path) -> None:
    _write(tmp_path, "wrong.yaml", VALID)
    with pytest.raises(RunbookError, match="does not match"):
        find_runbook("wrong", [tmp_path])


def test_shipped_example_runbooks_are_valid() -> None:
    examples = Path(__file__).parent.parent / "examples" / "runbooks"
    found = discover_runbooks([examples])
    assert set(found) == {"disk-pressure", "service-restart", "certificate-expiry"}
    for name in found:
        runbook = find_runbook(name, [examples])
        assert any(step.mutating for step in runbook.steps)
