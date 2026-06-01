"""Kubernetes access control helpers for the hardened in-cluster MCP path."""

from app.k8s.access import NamespaceAccessPolicy, NamespaceAccessError

__all__ = ["NamespaceAccessPolicy", "NamespaceAccessError"]
