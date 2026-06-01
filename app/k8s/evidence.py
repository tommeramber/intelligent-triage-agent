"""Classify Kubernetes MCP tool results for triage response provenance."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from app.models import KubernetesEvidence

# Tools that return only the namespace allowlist (not workload/cluster state).
ALLOWLIST_ONLY_TOOLS = frozenset({"list_accessible_namespaces"})

WORKLOAD_EVIDENCE_KEYS = frozenset({
    "pods",
    "events",
    "logs",
    "services",
    "replicas",
    "conditions",
})


class K8sToolOutcome(str, Enum):
    """Per-call classification used to aggregate run-level K8s evidence status."""

    NONE = "none"
    ALLOWLIST_ONLY = "allowlist_only"
    WORKLOAD_EVIDENCE = "workload_evidence"
    ACCESS_DENIED = "access_denied"
    OTHER_ERROR = "other_error"


def _parse_result(result_str: str) -> dict[str, Any] | None:
    try:
        data = json.loads(result_str)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def is_access_denied_message(message: str) -> bool:
    """True when an error string indicates namespace opt-in / guard rejection."""
    lower = (message or "").lower()
    return (
        "not opted in" in lower
        or "not accessible" in lower
        or "namespace argument is required" in lower
        or ("namespace" in lower and "required" in lower)
    )


def classify_k8s_tool_result(tool_name: str, result_str: str) -> K8sToolOutcome:
    """
    Classify one K8s MCP tool response.

    list_accessible_namespaces returns namespace names only — not workload evidence
    for the incident under triage.
    """
    data = _parse_result(result_str)
    if data is None:
        return K8sToolOutcome.NONE

    err = data.get("error")
    if err:
        if is_access_denied_message(str(err)):
            return K8sToolOutcome.ACCESS_DENIED
        return K8sToolOutcome.OTHER_ERROR

    if tool_name in ALLOWLIST_ONLY_TOOLS:
        if "namespaces" in data:
            return K8sToolOutcome.ALLOWLIST_ONLY
        return K8sToolOutcome.NONE

    if any(key in data for key in WORKLOAD_EVIDENCE_KEYS):
        return K8sToolOutcome.WORKLOAD_EVIDENCE

    return K8sToolOutcome.NONE


def finalize_kubernetes_evidence(
    invoked: list[str],
    *,
    workload_evidence: bool,
    access_denied: bool,
) -> KubernetesEvidence:
    """Build demo-friendly kubernetes provenance from aggregated tool outcomes."""
    if not invoked:
        return KubernetesEvidence(
            status="not_invoked",
            message=None,
            evidence_obtained=False,
            invoked=[],
        )

    if workload_evidence:
        return KubernetesEvidence(
            status="obtained",
            message="Kubernetes MCP returned workload/cluster evidence for an allowed namespace.",
            evidence_obtained=True,
            invoked=list(invoked),
        )

    if access_denied:
        return KubernetesEvidence(
            status="access_denied",
            message=(
                "Kubernetes MCP was invoked but could not inspect the requested namespace "
                "(not in allowed/opted-in namespaces)."
            ),
            evidence_obtained=False,
            invoked=list(invoked),
        )

    return KubernetesEvidence(
        status="no_accessible_evidence",
        message=(
            "Kubernetes MCP was invoked but no workload evidence was available "
            "for the requested target."
        ),
        evidence_obtained=False,
        invoked=list(invoked),
    )
