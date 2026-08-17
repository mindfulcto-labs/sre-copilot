"""Provider selection and fallback behaviour."""

from __future__ import annotations

import pytest

from sre_copilot.models import Alert, Category, TriageResult
from sre_copilot.triage.engine import triage_alert
from sre_copilot.triage.providers import (
    AnthropicProvider,
    OpenAIProvider,
    ProviderConfigError,
    ProviderError,
    resolve_provider,
)


class FailingProvider:
    name = "failing"

    def triage(self, alert: Alert) -> TriageResult:
        raise ProviderError("simulated outage")


class StubProvider:
    name = "stub"

    def triage(self, alert: Alert) -> TriageResult:
        return TriageResult(
            category=Category.LATENCY,
            likely_cause="stubbed",
            urgency="high",
            engine=self.name,
            confidence=0.9,
        )


def test_no_keys_means_rule_engine(disk_alert: Alert) -> None:
    assert resolve_provider() is None
    result = triage_alert(disk_alert)
    assert result.engine == "rules"
    assert result.category == Category.DISK


def test_provider_result_is_used_when_it_works(disk_alert: Alert) -> None:
    result = triage_alert(disk_alert, provider=StubProvider())
    assert result.engine == "stub"
    assert result.category == Category.LATENCY


def test_provider_failure_falls_back_to_rules(disk_alert: Alert) -> None:
    result = triage_alert(disk_alert, provider=FailingProvider())
    assert result.engine == "rules"
    assert result.category == Category.DISK


def test_forced_provider_without_key_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRE_COPILOT_PROVIDER", "openai")
    with pytest.raises(ProviderConfigError):
        resolve_provider()


def test_forced_rules_wins_over_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRE_COPILOT_PROVIDER", "rules")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert resolve_provider() is None


def test_key_selects_matching_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert isinstance(resolve_provider(), AnthropicProvider)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert isinstance(resolve_provider(), OpenAIProvider)


def test_unknown_provider_name_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRE_COPILOT_PROVIDER", "llamacpp")
    with pytest.raises(ProviderConfigError):
        resolve_provider()
