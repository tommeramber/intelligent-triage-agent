"""
Stdio MCP server for read-only Kubernetes triage evidence.

Uses the pod ServiceAccount (in-cluster config). Workload reads are limited to
namespaces opted in via triage.agent-accessible=true (RBAC + defense-in-depth here).
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.k8s.access import NamespaceAccessError, NamespaceAccessPolicy

logger = logging.getLogger(__name__)

mcp = FastMCP("triage-kubernetes")

_policy = NamespaceAccessPolicy.from_settings()
_core_v1: Any = None
_apps_v1: Any = None


def _apis() -> tuple[Any, Any]:
    global _core_v1, _apps_v1
    if _core_v1 is not None and _apps_v1 is not None:
        return _core_v1, _apps_v1

    from kubernetes import client, config
    from kubernetes.config.config_exception import ConfigException

    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config()

    _core_v1 = client.CoreV1Api()
    _apps_v1 = client.AppsV1Api()
    return _core_v1, _apps_v1


def _require_ns(namespace: str) -> str:
    ns = namespace.strip()
    _policy.require_namespace(ns)
    return ns


@mcp.tool(name="list_accessible_namespaces")
def list_accessible_namespaces() -> dict[str, Any]:
    """List namespaces the triage agent is allowed to inspect (opt-in label)."""
    names = sorted(_policy.get_allowlist())
    return {
        "namespaces": names,
        "label_selector": f"{_policy.label_key}={_policy.label_value}",
        "count": len(names),
    }


@mcp.tool(name="list_pods")
def list_pods(namespace: str, label_selector: str = "") -> dict[str, Any]:
    """List pods in an opted-in namespace (read-only)."""
    ns = _require_ns(namespace)
    core, _ = _apis()
    kwargs: dict[str, Any] = {}
    if label_selector.strip():
        kwargs["label_selector"] = label_selector.strip()
    pods = core.list_namespaced_pod(ns, **kwargs).items
    return {
        "namespace": ns,
        "pods": [
            {
                "name": p.metadata.name,
                "phase": p.status.phase,
                "ready": _pod_ready(p),
                "restarts": _pod_restarts(p),
                "reason": _pod_waiting_reason(p),
            }
            for p in pods
        ],
    }


@mcp.tool(name="get_pod_logs")
def get_pod_logs(
    namespace: str,
    pod_name: str,
    container: str = "",
    tail_lines: int = 80,
) -> dict[str, Any]:
    """Fetch recent pod logs (read-only) in an opted-in namespace."""
    ns = _require_ns(namespace)
    core, _ = _apis()
    tail = max(1, min(int(tail_lines), 500))
    kwargs: dict[str, Any] = {"tail_lines": tail}
    if container.strip():
        kwargs["container"] = container.strip()
    try:
        text = core.read_namespaced_pod_log(
            pod_name.strip(), ns, **kwargs
        )
    except Exception as exc:
        return {"namespace": ns, "pod": pod_name, "error": str(exc)}
    return {"namespace": ns, "pod": pod_name, "logs": text}


@mcp.tool(name="list_events")
def list_events(namespace: str, limit: int = 30) -> dict[str, Any]:
    """List recent events in an opted-in namespace (read-only)."""
    ns = _require_ns(namespace)
    core, _ = _apis()
    cap = max(1, min(int(limit), 100))
    events = core.list_namespaced_event(ns).items
    events.sort(
        key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp,
        reverse=True,
    )
    return {
        "namespace": ns,
        "events": [
            {
                "type": e.type,
                "reason": e.reason,
                "message": e.message,
                "involved_object": f"{e.involved_object.kind}/{e.involved_object.name}"
                if e.involved_object
                else "",
                "count": e.count,
            }
            for e in events[:cap]
        ],
    }


@mcp.tool(name="get_deployment_status")
def get_deployment_status(namespace: str, deployment_name: str) -> dict[str, Any]:
    """Get deployment status in an opted-in namespace (read-only)."""
    ns = _require_ns(namespace)
    _, apps = _apis()
    dep = apps.read_namespaced_deployment(deployment_name.strip(), ns)
    status = dep.status
    return {
        "namespace": ns,
        "name": dep.metadata.name,
        "replicas": status.replicas if status else None,
        "ready_replicas": status.ready_replicas if status else None,
        "available_replicas": status.available_replicas if status else None,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (status.conditions or [])
        ]
        if status
        else [],
    }


@mcp.tool(name="list_services")
def list_services(namespace: str) -> dict[str, Any]:
    """List services in an opted-in namespace (read-only)."""
    ns = _require_ns(namespace)
    core, _ = _apis()
    svcs = core.list_namespaced_service(ns).items
    return {
        "namespace": ns,
        "services": [
            {
                "name": s.metadata.name,
                "type": s.spec.type,
                "cluster_ip": s.spec.cluster_ip,
                "ports": [
                    f"{p.port}/{p.protocol}" for p in (s.spec.ports or [])
                ],
            }
            for s in svcs
        ],
    }


def _pod_ready(pod: Any) -> str:
    statuses = pod.status.container_statuses or []
    if not statuses:
        return "unknown"
    ready = sum(1 for s in statuses if s.ready)
    return f"{ready}/{len(statuses)}"


def _pod_restarts(pod: Any) -> int:
    total = 0
    for s in pod.status.container_statuses or []:
        if s.restart_count:
            total += s.restart_count
    return total


def _pod_waiting_reason(pod: Any) -> str:
    for s in pod.status.container_statuses or []:
        state = s.state
        if state and state.waiting and state.waiting.reason:
            return state.waiting.reason
    return ""


if __name__ == "__main__":
    try:
        _policy.get_allowlist()
    except NamespaceAccessError:
        pass
    except Exception as exc:
        logger.warning("Could not warm namespace allowlist at startup: %s", exc)
    mcp.run(transport="stdio")
