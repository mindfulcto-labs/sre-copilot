"""Pluggable LLM providers for triage.

Providers are optional. When no API key is configured the tool runs the
deterministic rule engine instead, so nothing here is needed for offline
use or for the test suite.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

import httpx

from sre_copilot.models import Alert, Category, TriageResult, Urgency

KNOWN_RUNBOOKS = ("disk-pressure", "service-restart", "certificate-expiry")

_INSTRUCTIONS = (
    "You classify infrastructure alerts for a site reliability engineer. "
    "Reply with a single JSON object and nothing else. Keys: "
    "category (one of: " + ", ".join(c.value for c in Category) + "), "
    "likely_cause (one short sentence), "
    "urgency (one of: " + ", ".join(u.value for u in Urgency) + "), "
    "recommended_runbook (one of: " + ", ".join(KNOWN_RUNBOOKS) + ", or null), "
    "confidence (a number between 0 and 1)."
)


class ProviderError(RuntimeError):
    """A provider call failed. The engine falls back to the rule engine."""


class ProviderConfigError(RuntimeError):
    """The provider selection or credentials are wrong. Not silently swallowed."""


class TriageProvider(Protocol):
    """Anything that can turn an Alert into a TriageResult."""

    name: str

    def triage(self, alert: Alert) -> TriageResult: ...


def _alert_prompt(alert: Alert) -> str:
    return (
        "Classify this alert.\n"
        f"source: {alert.source}\n"
        f"severity: {alert.severity.value}\n"
        f"service: {alert.service}\n"
        f"message: {alert.message}\n"
        f"labels: {json.dumps(alert.labels, sort_keys=True)}"
    )


def _parse_result(raw: str, engine: str) -> TriageResult:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"{engine}: reply was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProviderError(f"{engine}: reply was not a JSON object")
    runbook = data.get("recommended_runbook")
    if runbook is not None and runbook not in KNOWN_RUNBOOKS:
        runbook = None
    try:
        return TriageResult(
            category=data["category"],
            likely_cause=str(data.get("likely_cause", "")).strip() or "No cause given.",
            urgency=data["urgency"],
            recommended_runbook=runbook,
            engine=engine,
            confidence=float(data.get("confidence", 0.5)),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ProviderError(f"{engine}: reply failed validation: {exc}") from exc


class OpenAIProvider:
    """Triage via the OpenAI chat completions API."""

    name = "openai"

    def __init__(self, api_key: str, model: str | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.model = model or os.environ.get("SRE_COPILOT_MODEL") or "gpt-4o-mini"
        self.timeout = timeout

    def triage(self, alert: Alert) -> TriageResult:
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _INSTRUCTIONS},
                {"role": "user", "content": _alert_prompt(alert)},
            ],
        }
        try:
            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"openai: request failed: {exc}") from exc
        return _parse_result(content, self.name)


class AnthropicProvider:
    """Triage via the Anthropic messages API."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.model = model or os.environ.get("SRE_COPILOT_MODEL") or "claude-3-5-haiku-latest"
        self.timeout = timeout

    def triage(self, alert: Alert) -> TriageResult:
        payload = {
            "model": self.model,
            "max_tokens": 512,
            "system": _INSTRUCTIONS,
            "messages": [{"role": "user", "content": _alert_prompt(alert)}],
        }
        try:
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json()["content"][0]["text"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"anthropic: request failed: {exc}") from exc
        return _parse_result(content, self.name)


def resolve_provider() -> TriageProvider | None:
    """Pick a provider from the environment, or None for the rule engine.

    SRE_COPILOT_PROVIDER forces a choice: 'openai', 'anthropic' or 'rules'.
    Without it, the first available API key wins (Anthropic, then OpenAI).
    A forced choice with a missing key raises ProviderConfigError rather
    than silently falling back.
    """
    choice = os.environ.get("SRE_COPILOT_PROVIDER", "").strip().lower()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if choice == "rules":
        return None
    if choice == "anthropic":
        if not anthropic_key:
            raise ProviderConfigError(
                "SRE_COPILOT_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set"
            )
        return AnthropicProvider(anthropic_key)
    if choice == "openai":
        if not openai_key:
            raise ProviderConfigError("SRE_COPILOT_PROVIDER=openai but OPENAI_API_KEY is not set")
        return OpenAIProvider(openai_key)
    if choice:
        raise ProviderConfigError(
            f"unknown SRE_COPILOT_PROVIDER '{choice}' (use openai, anthropic or rules)"
        )

    if anthropic_key:
        return AnthropicProvider(anthropic_key)
    if openai_key:
        return OpenAIProvider(openai_key)
    return None
