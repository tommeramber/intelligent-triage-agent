"""Tests for namespace opt-in policy and hub guard."""

import json

import pytest

from app.k8s.access import NamespaceAccessError, NamespaceAccessPolicy
from app.k8s.guard import enforce_k8s_tool_namespace, reset_policy_for_tests


class TestNamespaceAccessPolicy:
    def test_static_allowlist_allows_only_listed(self):
        policy = NamespaceAccessPolicy(allowlist=frozenset({"triage-demo"}))
        assert policy.is_allowed("triage-demo")
        assert not policy.is_allowed("kube-system")

    def test_require_namespace_raises_for_opted_out(self):
        policy = NamespaceAccessPolicy(allowlist=frozenset({"triage-demo"}))
        with pytest.raises(NamespaceAccessError, match="not opted in"):
            policy.require_namespace("default")

    def test_empty_namespace_rejected(self):
        policy = NamespaceAccessPolicy(allowlist=frozenset({"triage-demo"}))
        with pytest.raises(NamespaceAccessError, match="required"):
            policy.require_namespace("")


class TestK8sGuard:
    def setup_method(self) -> None:
        reset_policy_for_tests(
            NamespaceAccessPolicy(allowlist=frozenset({"triage-demo"}))
        )

    def teardown_method(self) -> None:
        reset_policy_for_tests(None)

    def test_blocks_non_allowlisted_namespace(self):
        err = enforce_k8s_tool_namespace("list_pods", {"namespace": "kube-system"})
        assert err is not None
        assert "not opted in" in err

    def test_allows_listed_namespace(self):
        assert enforce_k8s_tool_namespace("list_pods", {"namespace": "triage-demo"}) is None

    def test_list_accessible_namespaces_not_guarded(self):
        assert enforce_k8s_tool_namespace("list_accessible_namespaces", {}) is None


@pytest.mark.asyncio
async def test_hub_blocks_k8s_tool_for_wrong_namespace():
    """Hub refuses K8s MCP calls before subprocess when namespace is not opted in."""
    reset_policy_for_tests(
        NamespaceAccessPolicy(allowlist=frozenset({"triage-demo"}))
    )

    from app.mcp.hub import McpHub

    class _FakeK8s:
        available = True

        def owns_tool(self, name: str) -> bool:
            return name == "list_pods"

        async def call_tool(self, name: str, arguments: dict) -> str:
            return json.dumps({"unexpected": "subprocess should not run"})

    hub = McpHub()
    hub._k8s = _FakeK8s()
    try:
        raw = await hub.call_tool("list_pods", {"namespace": "default"})
        data = json.loads(raw)
        assert "error" in data
        assert "not opted in" in data["error"]
    finally:
        reset_policy_for_tests(None)
