"""
Precondition for exposing Kubernetes MCP tools to the LLM.

Cluster tools are only offered when the error text names a concrete target
(namespace, pod, or workload). Vague cluster-wide wording must not trigger K8s MCP.
"""

from __future__ import annotations

import re

# Captured identifiers must not be generic K8s vocabulary or sentence filler.
_INVALID_NAMES = frozenset({
    "a", "an", "the", "this", "that", "my", "your", "our",
    "cluster", "kubernetes", "k8s", "node", "nodes",
    "pod", "pods", "container", "containers",
    "service", "services", "deployment", "deployments",
    "namespace", "namespaces", "workload", "workloads",
    "down", "up", "failed", "error", "status", "state",
})

_NAMESPACE_PATTERNS = (
    re.compile(r"\bnamespace[:\s]+([a-z0-9][-a-z0-9]*)\b", re.I),
    re.compile(r"\bin\s+([a-z0-9][-a-z0-9]*)\s+namespace\b", re.I),
    re.compile(r"\b([a-z0-9][-a-z0-9]*)\s+namespace\b", re.I),
    re.compile(
        r"\b(?:pods?|events?|logs?|deployments?|services?)\s+in\s+([a-z0-9][-a-z0-9]*)\b",
        re.I,
    ),
)

_POD_PATTERNS = (
    re.compile(r"\bpod\s+([a-z0-9][-a-z0-9.]*)\b", re.I),
    re.compile(r"\bpods?\s+named\s+([a-z0-9][-a-z0-9.]*)\b", re.I),
)

_WORKLOAD_PATTERNS = (
    re.compile(
        r"\b(?:deployment|deploy|statefulset|daemonset|replicaset|sts|ds)\s+([a-z0-9][-a-z0-9]*)\b",
        re.I,
    ),
    re.compile(r"\bworkload\s+([a-z0-9][-a-z0-9]*)\b", re.I),
)

_K8S_CONTEXT = re.compile(
    r"\b(?:pod|pods|crashloop|oomkilled|pending|evicted|namespace|"
    r"deploy(?:ment)?|kubernetes|k8s|scheduler|container)\b",
    re.I,
)

# Replica-set style pod names (e.g. api-7d4f8b9c2-xk9lm) when K8s context is present.
_POD_LIKE_NAME = re.compile(
    r"\b([a-z0-9][-a-z0-9]*-[a-z0-9]{5,}(?:[-a-z0-9]*)?)\b",
    re.I,
)

K8S_GROUNDING_BLOCK_REASON = (
    "Kubernetes MCP is withheld: the error description does not name a "
    "namespace, pod, or workload to inspect."
)


def _valid_resource_name(name: str) -> bool:
    candidate = name.strip().lower()
    if len(candidate) < 2 or candidate in _INVALID_NAMES:
        return False
    return bool(re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", candidate))


def _any_named_match(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    for pattern in patterns:
        for match in pattern.finditer(text):
            if _valid_resource_name(match.group(1)):
                return True
    return False


def input_has_k8s_grounding(text: str) -> bool:
    """
    True when the incident text identifies a concrete Kubernetes inspection target.

    Examples that qualify: ``triage-demo namespace``, ``pod payments-api-7d4f8b``,
    ``deployment checkout``, ``pods in kube-system``.
    Examples that do not: ``node is down in cluster``, generic infra wording alone.
    """
    if not text or not text.strip():
        return False

    if _any_named_match(_NAMESPACE_PATTERNS, text):
        return True
    if _any_named_match(_POD_PATTERNS, text):
        return True
    if _any_named_match(_WORKLOAD_PATTERNS, text):
        return True

    if _K8S_CONTEXT.search(text):
        for match in _POD_LIKE_NAME.finditer(text):
            if _valid_resource_name(match.group(1)):
                return True

    return False
