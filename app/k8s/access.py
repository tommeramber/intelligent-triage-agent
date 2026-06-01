"""
Namespace opt-in policy for hardened Kubernetes MCP.

Enforcement layers:
  1. RBAC — RoleBindings only in namespaces labeled triage.agent-accessible=true
  2. This module — refuse workload inspection outside the allowlist
  3. MCP server + agent hub — call sites use the same checks

The allowlist comes from K8S_ACCESS_ALLOWLIST (synced by make k8s-sync-access) or,
when empty, is discovered live via the API using the configured label selector
(in-cluster ServiceAccount must have namespaces list permission).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from kubernetes.client import CoreV1Api

logger = logging.getLogger(__name__)

DEFAULT_LABEL_KEY = "triage.agent-accessible"
DEFAULT_LABEL_VALUE = "true"
_CACHE_TTL_SECONDS = 30.0


class NamespaceAccessError(ValueError):
    """Raised when a namespace is not opted in for triage inspection."""


class NamespaceAccessPolicy:
    """Determines which namespaces the triage agent may inspect (read-only)."""

    def __init__(
        self,
        *,
        allowlist: frozenset[str] | None = None,
        label_key: str | None = None,
        label_value: str | None = None,
        core_api: CoreV1Api | None = None,
    ) -> None:
        self._static_allowlist = allowlist
        self.label_key = label_key or settings.k8s_access_label_key
        self.label_value = label_value or settings.k8s_access_label_value
        self._core_api = core_api
        self._cached: frozenset[str] | None = None
        self._cached_at: float = 0.0

    @classmethod
    def from_settings(cls, core_api: CoreV1Api | None = None) -> NamespaceAccessPolicy:
        static = settings.k8s_access_allowlist_set()
        return cls(
            allowlist=frozenset(static) if static else None,
            core_api=core_api,
        )

    def is_allowed(self, namespace: str) -> bool:
        ns = (namespace or "").strip()
        if not ns:
            return False
        return ns in self.get_allowlist()

    def require_namespace(self, namespace: str) -> None:
        ns = (namespace or "").strip()
        if not ns:
            raise NamespaceAccessError("namespace is required")
        if not self.is_allowed(ns):
            raise NamespaceAccessError(
                f"namespace {ns!r} is not opted in "
                f"(label {self.label_key}={self.label_value!r} required)"
            )

    def get_allowlist(self) -> frozenset[str]:
        if self._static_allowlist is not None:
            return self._static_allowlist

        now = time.monotonic()
        if self._cached is not None and (now - self._cached_at) < _CACHE_TTL_SECONDS:
            return self._cached

        discovered = frozenset(self._discover_via_api())
        self._cached = discovered
        self._cached_at = now
        return discovered

    def invalidate_cache(self) -> None:
        self._cached = None
        self._cached_at = 0.0

    def _discover_via_api(self) -> set[str]:
        api = self._core_api or _load_core_v1()
        if api is None:
            logger.warning(
                "K8s namespace discovery unavailable — no API client and no static allowlist"
            )
            return set()

        selector = f"{self.label_key}={self.label_value}"
        items = api.list_namespace(label_selector=selector).items
        names = {ns.metadata.name for ns in items if ns.metadata and ns.metadata.name}
        logger.debug("Discovered %d accessible namespace(s): %s", len(names), sorted(names))
        return names


def _load_core_v1() -> CoreV1Api | None:
    try:
        from kubernetes import client, config
        from kubernetes.config.config_exception import ConfigException

        try:
            config.load_incluster_config()
        except ConfigException:
            try:
                config.load_kube_config()
            except ConfigException:
                return None
        return client.CoreV1Api()
    except ImportError:
        return None
