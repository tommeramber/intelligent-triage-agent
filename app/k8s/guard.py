"""Defense-in-depth checks for Kubernetes MCP tool calls in the agent hub."""

from __future__ import annotations

from typing import Any

from app.k8s.access import NamespaceAccessError, NamespaceAccessPolicy

# Tools that accept a namespace argument (must match mcp_servers.k8s_server).
K8S_NAMESPACE_TOOLS = frozenset({
    "list_pods",
    "get_pod_logs",
    "list_events",
    "get_deployment_status",
    "list_services",
})

_NAMESPACE_ARG = "namespace"

_policy: NamespaceAccessPolicy | None = None


def _policy_instance() -> NamespaceAccessPolicy:
    global _policy
    if _policy is None:
        _policy = NamespaceAccessPolicy.from_settings()
    return _policy


def reset_policy_for_tests(policy: NamespaceAccessPolicy | None = None) -> None:
    """Reset cached policy (tests only)."""
    global _policy
    _policy = policy


def enforce_k8s_tool_namespace(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """
    Return an error message if the tool call must be blocked; None if allowed.

    list_accessible_namespaces has no namespace parameter and is always allowed.
    """
    if tool_name not in K8S_NAMESPACE_TOOLS:
        return None

    namespace = arguments.get(_NAMESPACE_ARG)
    if not isinstance(namespace, str) or not namespace.strip():
        return "namespace argument is required for this tool"

    try:
        _policy_instance().require_namespace(namespace.strip())
    except NamespaceAccessError as exc:
        return str(exc)
    return None
