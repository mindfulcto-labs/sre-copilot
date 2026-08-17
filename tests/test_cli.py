"""End-to-end CLI behaviour through Typer's test runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sre_copilot.cli import app

runner = CliRunner()

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run the CLI from a temp directory wired to the shipped examples."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SRE_COPILOT_RUNBOOK_DIRS", str(REPO_ROOT / "examples" / "runbooks"))
    monkeypatch.setenv("SRE_COPILOT_AUDIT_DIR", str(tmp_path / "audit"))
    # Keep rich from truncating table cells in the narrow test terminal.
    monkeypatch.setenv("COLUMNS", "200")
    return tmp_path


def _alert_file(directory: Path) -> Path:
    path = directory / "alert.json"
    path.write_text(
        json.dumps(
            {
                "source": "prometheus",
                "severity": "critical",
                "service": "payments-api",
                "message": "Filesystem /var is 96% full, disk pressure",
                "labels": {"env": "prod"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_triage_command_recommends_runbook(workdir: Path) -> None:
    result = runner.invoke(app, ["triage", str(_alert_file(workdir))])
    assert result.exit_code == 0
    assert "disk" in result.output
    assert "disk-pressure" in result.output
    assert "rules" in result.output


def test_triage_json_output_is_machine_readable(workdir: Path) -> None:
    result = runner.invoke(app, ["triage", str(_alert_file(workdir)), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["category"] == "disk"
    assert payload["recommended_runbook"] == "disk-pressure"


def test_triage_rejects_bad_alert_file(workdir: Path) -> None:
    bad = workdir / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = runner.invoke(app, ["triage", str(bad)])
    assert result.exit_code == 2


def test_runbook_list_shows_shipped_examples(workdir: Path) -> None:
    result = runner.invoke(app, ["runbook", "list"])
    assert result.exit_code == 0
    for name in ("disk-pressure", "service-restart", "certificate-expiry"):
        assert name in result.output


def test_runbook_run_dry_run_writes_audit_then_history_shows_it(workdir: Path) -> None:
    alert = _alert_file(workdir)
    result = runner.invoke(
        app, ["runbook", "run", "disk-pressure", "--alert", str(alert), "--dry-run"]
    )
    assert result.exit_code == 0
    assert "dry run" in result.output

    history = runner.invoke(app, ["history"])
    assert history.exit_code == 0
    assert "disk-pressure" in history.output
    assert "skipped" in history.output


def test_runbook_run_unknown_name_fails_cleanly(workdir: Path) -> None:
    result = runner.invoke(app, ["runbook", "run", "does-not-exist"])
    assert result.exit_code == 2
    assert "no runbook named" in result.output


def test_history_with_no_records(workdir: Path) -> None:
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 0
    assert "No audit records yet" in result.output
