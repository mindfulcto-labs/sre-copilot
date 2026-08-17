"""Deterministic rule-based triage.

This engine works offline, needs no API key, and always returns the same
result for the same alert. It is the fallback when no LLM provider is
configured or when a provider call fails, and it is what the tests and
the evals harness run against.
"""

from __future__ import annotations

from dataclasses import dataclass

from sre_copilot.models import Alert, Category, Severity, TriageResult, Urgency

ENGINE_NAME = "rules"


@dataclass(frozen=True)
class Rule:
    """One classification rule: keywords that vote for a category."""

    category: Category
    keywords: tuple[str, ...]
    cause: str
    runbook: str | None


# Order matters: on a tied keyword count the earlier rule wins.
RULES: tuple[Rule, ...] = (
    Rule(
        category=Category.DISK,
        keywords=(
            "disk", "filesystem", "no space", "out of space", "inode", "volume full", "df ",
        ),
        cause="A filesystem is at or near capacity, usually from log growth or unrotated data.",
        runbook="disk-pressure",
    ),
    Rule(
        category=Category.CERTIFICATE,
        keywords=(
            "certificate", "cert ", "tls", "ssl", "x509", "expiry", "expires", "expired",
        ),
        cause="A TLS certificate has expired or is close to its expiry date.",
        runbook="certificate-expiry",
    ),
    Rule(
        category=Category.AVAILABILITY,
        keywords=(
            "down", "unreachable", "connection refused", "crashloop", "crash loop",
            "health check", "healthcheck", "5xx", "503", "502", "not responding", "restarting",
        ),
        cause="The service is not answering requests, often after a crash or a bad deploy.",
        runbook="service-restart",
    ),
    Rule(
        category=Category.MEMORY,
        keywords=("oom", "out of memory", "memory", "rss", "heap", "swap"),
        cause="The process is running out of memory or being killed by the OOM killer.",
        runbook="service-restart",
    ),
    Rule(
        category=Category.LATENCY,
        keywords=("latency", "slow", "p99", "p95", "response time", "queue depth", "saturat"),
        cause="Requests are taking longer than the service level objective allows.",
        runbook=None,
    ),
    Rule(
        category=Category.NETWORK,
        keywords=("dns", "packet loss", "network", "route", "timeout", "unreachable host"),
        cause="A network path or name resolution problem between services.",
        runbook=None,
    ),
)

_SEVERITY_TO_URGENCY: dict[Severity, Urgency] = {
    Severity.CRITICAL: Urgency.IMMEDIATE,
    Severity.WARNING: Urgency.HIGH,
    Severity.INFO: Urgency.ROUTINE,
}


def _alert_text(alert: Alert) -> str:
    label_text = " ".join(f"{k}={v}" for k, v in sorted(alert.labels.items()))
    return f"{alert.message} {label_text}".lower()


def classify(alert: Alert) -> TriageResult:
    """Classify an alert with keyword rules. Deterministic, offline, no keys."""
    text = _alert_text(alert)
    best_rule: Rule | None = None
    best_hits = 0
    for rule in RULES:
        hits = sum(1 for kw in rule.keywords if kw in text)
        if hits > best_hits:
            best_rule = rule
            best_hits = hits

    if best_rule is None:
        return TriageResult(
            category=Category.UNKNOWN,
            likely_cause="No rule matched this alert. A human should read it.",
            urgency=_urgency_for(alert, matched=False),
            recommended_runbook=None,
            engine=ENGINE_NAME,
            confidence=0.2,
        )

    confidence = min(0.9, 0.5 + 0.1 * (best_hits - 1))
    return TriageResult(
        category=best_rule.category,
        likely_cause=best_rule.cause,
        urgency=_urgency_for(alert, matched=True),
        recommended_runbook=best_rule.runbook,
        engine=ENGINE_NAME,
        confidence=confidence,
    )


def _urgency_for(alert: Alert, *, matched: bool) -> Urgency:
    urgency = _SEVERITY_TO_URGENCY[alert.severity]
    # Production alerts that matched a rule never sit at routine urgency.
    if matched and urgency is Urgency.ROUTINE and alert.labels.get("env") == "prod":
        return Urgency.HIGH
    return urgency
