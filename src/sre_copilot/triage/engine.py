"""Triage orchestration: try the configured LLM provider, fall back to rules."""

from __future__ import annotations

import logging

from sre_copilot.models import Alert, TriageResult
from sre_copilot.triage import rules
from sre_copilot.triage.providers import (
    ProviderError,
    TriageProvider,
    resolve_provider,
)

logger = logging.getLogger(__name__)


def triage_alert(alert: Alert, provider: TriageProvider | None = None) -> TriageResult:
    """Triage one alert.

    When ``provider`` is None the environment decides (see resolve_provider).
    A provider failure is logged and the deterministic rule engine answers
    instead, so triage always returns a result. Misconfiguration
    (ProviderConfigError from resolve_provider) is deliberately not caught.
    """
    if provider is None:
        provider = resolve_provider()
    if provider is not None:
        try:
            return provider.triage(alert)
        except ProviderError as exc:
            logger.warning("provider %s failed, falling back to rules: %s", provider.name, exc)
    return rules.classify(alert)
