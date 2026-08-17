"""Command line interface for sre-copilot."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from sre_copilot import __version__
from sre_copilot.audit import AuditLog
from sre_copilot.models import Alert, StepStatus
from sre_copilot.runbooks import RunbookError, RunbookExecutor, discover_runbooks, find_runbook
from sre_copilot.triage import triage_alert
from sre_copilot.triage.providers import ProviderConfigError

app = typer.Typer(
    help="Incident triage and runbook execution for SRE work.",
    no_args_is_help=True,
    add_completion=False,
)
runbook_app = typer.Typer(help="List and run runbooks.", no_args_is_help=True)
app.add_typer(runbook_app, name="runbook")

console = Console()
err_console = Console(stderr=True)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def _load_alert(path: Path) -> Alert:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        err_console.print(f"[red]cannot read alert file:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except json.JSONDecodeError as exc:
        err_console.print(f"[red]{path} is not valid JSON:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    try:
        return Alert.model_validate(raw)
    except ValidationError as exc:
        err_console.print(f"[red]{path} is not a valid alert:[/red]\n{exc}")
        raise typer.Exit(code=2) from exc


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"sre-copilot {__version__}")


@app.command()
def triage(
    alert_file: Annotated[Path, typer.Argument(help="Path to an alert JSON file.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Classify an alert and recommend a runbook."""
    alert = _load_alert(alert_file)
    try:
        result = triage_alert(alert)
    except ProviderConfigError as exc:
        err_console.print(f"[red]provider configuration error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if as_json:
        console.print_json(result.model_dump_json())
        return

    table = Table(title=f"Triage: {alert.service} ({alert.severity.value})", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("Message", alert.message)
    table.add_row("Category", result.category.value)
    table.add_row("Likely cause", result.likely_cause)
    table.add_row("Urgency", result.urgency.value)
    table.add_row("Recommended runbook", result.recommended_runbook or "none")
    table.add_row("Engine", f"{result.engine} (confidence {result.confidence:.2f})")
    console.print(table)


@runbook_app.command("list")
def runbook_list() -> None:
    """List runbooks found in the search directories."""
    found = discover_runbooks()
    if not found:
        console.print("No runbooks found. Set SRE_COPILOT_RUNBOOK_DIRS or add ./runbooks.")
        return
    table = Table(title="Runbooks")
    table.add_column("name", style="bold")
    table.add_column("path")
    for name in sorted(found):
        table.add_row(name, str(found[name]))
    console.print(table)


@runbook_app.command("run")
def runbook_run(
    name: Annotated[str, typer.Argument(help="Runbook name, e.g. disk-pressure.")],
    alert_file: Annotated[
        Path | None,
        typer.Option("--alert", help="Alert JSON file passed to steps as SRE_ALERT_* env vars."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Describe every step without executing anything.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Auto-approve mutating steps.")
    ] = False,
) -> None:
    """Run a runbook step by step, with an approval gate on mutating steps."""
    try:
        runbook = find_runbook(name)
    except RunbookError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    alert = _load_alert(alert_file) if alert_file is not None else None

    def report(step: object, result: object) -> None:
        colours = {
            StepStatus.OK: "green",
            StepStatus.SKIPPED: "yellow",
            StepStatus.DECLINED: "yellow",
            StepStatus.FAILED: "red",
        }
        colour = colours.get(result.status, "white")  # type: ignore[attr-defined]
        console.print(
            f"[{colour}]{result.status.value:8}[/{colour}] {result.step}"  # type: ignore[attr-defined]
            + (f"  ({result.detail})" if result.detail else "")  # type: ignore[attr-defined]
        )

    executor = RunbookExecutor(
        AuditLog(),
        dry_run=dry_run,
        auto_approve=yes,
        confirm=lambda question: typer.confirm(question),
        on_step=report,
    )
    console.print(f"Running runbook [bold]{runbook.name}[/bold]: {runbook.description}")
    results = executor.run(runbook, alert)
    if any(r.status in (StepStatus.FAILED, StepStatus.DECLINED) for r in results):
        raise typer.Exit(code=1)


@app.command()
def history() -> None:
    """Print the audit log as a table."""
    log = AuditLog()
    try:
        records = log.read()
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    if not records:
        console.print(f"No audit records yet (looked in {log.path}).")
        return
    table = Table(title=f"Audit log: {log.path}")
    table.add_column("timestamp (UTC)")
    table.add_column("runbook")
    table.add_column("step")
    table.add_column("status")
    table.add_column("approved by")
    table.add_column("dry run")
    for record in records:
        table.add_row(
            record.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            record.runbook,
            record.step,
            record.status.value,
            record.approved_by or "-",
            "yes" if record.dry_run else "no",
        )
    console.print(table)


if __name__ == "__main__":
    app()
