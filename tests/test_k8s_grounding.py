"""Tests for Kubernetes MCP input grounding gate."""

import pytest

from app.k8s.grounding import input_has_k8s_grounding


class TestInputHasK8sGrounding:
    @pytest.mark.parametrize(
        "text",
        [
            "pods in triage-demo namespace CrashLoopBackOff",
            "list pods and events in kube-system namespace",
            "namespace: triage-demo",
            "in triage-demo namespace",
            "pod payments-api-7d4f8b2c-xk9lm OOMKilled",
            "deployment checkout failing",
            "events in staging",
            "503: No healthy upstream — inspect triage-demo namespace",
        ],
    )
    def test_grounded_inputs(self, text: str) -> None:
        assert input_has_k8s_grounding(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "node is down in cluster",
            "DB connection refused to postgres:5432",
            "cluster-wide networking degradation",
            "kubernetes control plane instability",
            "Pods stuck Pending — scheduler reports FailedScheduling",
            "generic internal server error",
            "",
            "   ",
        ],
    )
    def test_vague_inputs(self, text: str) -> None:
        assert input_has_k8s_grounding(text) is False
