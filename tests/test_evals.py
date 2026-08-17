"""The evals harness runs offline and the rule engine clears its own bar."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "evals"))

from run_evals import ACCURACY_THRESHOLD, evaluate, load_fixtures  # noqa: E402


def test_fixtures_load_and_are_labelled() -> None:
    pairs = load_fixtures()
    assert len(pairs) == 15
    assert all(expected is not None for _, expected in pairs)


def test_rule_engine_meets_accuracy_threshold() -> None:
    report = evaluate(load_fixtures())
    assert report.total == 15
    assert report.accuracy >= ACCURACY_THRESHOLD


def test_matched_categories_have_perfect_precision_on_fixtures() -> None:
    report = evaluate(load_fixtures())
    by_name = {score.category: score for score in report.scores}
    for category in ("disk", "certificate", "availability"):
        assert by_name[category].precision == 1.0
