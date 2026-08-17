"""Replay labelled alert fixtures through the rule engine and report precision.

Runs fully offline against the deterministic rule engine, so the numbers
are reproducible on any machine with no API keys. Exits non-zero when
overall accuracy drops below the threshold, which makes it usable as a
regression gate in CI.

Usage:
    python evals/run_evals.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sre_copilot.models import Alert, Category
from sre_copilot.triage.rules import classify

FIXTURES = Path(__file__).parent / "fixtures" / "labelled_alerts.jsonl"
ACCURACY_THRESHOLD = 0.8


@dataclass(frozen=True)
class CategoryScore:
    category: str
    precision: float | None
    recall: float | None
    support: int


@dataclass(frozen=True)
class EvalReport:
    total: int
    correct: int
    scores: list[CategoryScore]

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


def load_fixtures(path: Path = FIXTURES) -> list[tuple[Alert, Category]]:
    """Load (alert, expected category) pairs from the JSONL fixture file."""
    pairs: list[tuple[Alert, Category]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            alert = Alert.model_validate(raw["alert"])
            expected = Category(raw["expected_category"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{path}:{line_number}: bad fixture: {exc}") from exc
        pairs.append((alert, expected))
    return pairs


def evaluate(pairs: list[tuple[Alert, Category]]) -> EvalReport:
    """Score the rule engine against the labelled pairs."""
    true_positives: Counter[Category] = Counter()
    predicted: Counter[Category] = Counter()
    actual: Counter[Category] = Counter()
    correct = 0
    for alert, expected in pairs:
        result = classify(alert)
        predicted[result.category] += 1
        actual[expected] += 1
        if result.category == expected:
            true_positives[expected] += 1
            correct += 1

    scores: list[CategoryScore] = []
    for category in Category:
        support = actual[category]
        n_predicted = predicted[category]
        if support == 0 and n_predicted == 0:
            continue
        precision = true_positives[category] / n_predicted if n_predicted else None
        recall = true_positives[category] / support if support else None
        scores.append(
            CategoryScore(
                category=category.value,
                precision=precision,
                recall=recall,
                support=support,
            )
        )
    return EvalReport(total=len(pairs), correct=correct, scores=scores)


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "n/a"


def print_report(report: EvalReport) -> None:
    header = f"{'category':<14} {'precision':>9} {'recall':>7} {'support':>8}"
    print(header)
    print("-" * len(header))
    for score in report.scores:
        print(
            f"{score.category:<14} {_fmt(score.precision):>9} "
            f"{_fmt(score.recall):>7} {score.support:>8}"
        )
    print("-" * len(header))
    print(f"overall accuracy: {report.correct}/{report.total} = {report.accuracy:.2f}")


def main() -> int:
    report = evaluate(load_fixtures())
    print_report(report)
    if report.accuracy < ACCURACY_THRESHOLD:
        print(f"FAIL: accuracy below threshold {ACCURACY_THRESHOLD:.2f}")
        return 1
    print(f"PASS: accuracy at or above threshold {ACCURACY_THRESHOLD:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
