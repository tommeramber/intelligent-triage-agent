"""Tests for Kubernetes MCP result classification and response provenance."""

import json

from app.k8s.evidence import (
    K8sToolOutcome,
    classify_k8s_tool_result,
    finalize_kubernetes_evidence,
    is_access_denied_message,
)


class TestIsAccessDeniedMessage:
    def test_opted_in_wording(self):
        assert is_access_denied_message("namespace 'default' is not opted in (label ...)")

    def test_legacy_accessible_wording(self):
        assert is_access_denied_message("namespace 'default' is not accessible")


class TestClassifyK8sToolResult:
    def test_allowlist_only_not_workload_evidence(self):
        payload = json.dumps({"namespaces": ["triage-demo"], "count": 1})
        assert (
            classify_k8s_tool_result("list_accessible_namespaces", payload)
            == K8sToolOutcome.ALLOWLIST_ONLY
        )

    def test_list_pods_with_pods_is_workload_evidence(self):
        payload = json.dumps({"namespace": "triage-demo", "pods": []})
        assert (
            classify_k8s_tool_result("list_pods", payload)
            == K8sToolOutcome.WORKLOAD_EVIDENCE
        )

    def test_guard_error_is_access_denied(self):
        payload = json.dumps({"error": "namespace 'kube-system' is not opted in (label ...)"})
        assert (
            classify_k8s_tool_result("list_pods", payload)
            == K8sToolOutcome.ACCESS_DENIED
        )

    def test_generic_error_is_other(self):
        payload = json.dumps({"error": "connection refused"})
        assert (
            classify_k8s_tool_result("list_pods", payload)
            == K8sToolOutcome.OTHER_ERROR
        )


class TestFinalizeKubernetesEvidence:
    def test_not_invoked(self):
        ev = finalize_kubernetes_evidence([], workload_evidence=False, access_denied=False)
        assert ev.status == "not_invoked"
        assert ev.evidence_obtained is False
        assert ev.message is None

    def test_obtained(self):
        ev = finalize_kubernetes_evidence(
            ["list_pods"],
            workload_evidence=True,
            access_denied=False,
        )
        assert ev.status == "obtained"
        assert ev.evidence_obtained is True
        assert ev.message

    def test_access_denied_over_no_evidence(self):
        ev = finalize_kubernetes_evidence(
            ["list_accessible_namespaces", "list_pods"],
            workload_evidence=False,
            access_denied=True,
        )
        assert ev.status == "access_denied"
        assert ev.evidence_obtained is False

    def test_no_accessible_evidence(self):
        ev = finalize_kubernetes_evidence(
            ["list_accessible_namespaces"],
            workload_evidence=False,
            access_denied=False,
        )
        assert ev.status == "no_accessible_evidence"
        assert ev.evidence_obtained is False
