"""API contract tests — validation and OpenAPI shape (offline, no LLM)."""

import pytest
from pydantic import ValidationError

from app.main import app
from app.models import EvidenceSources, TriageRequest, TriageResponse


class TestTriageRequestValidation:
    def test_unknown_error_code_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            TriageRequest({"999": "This code is not in the allowed set"})
        assert "Unknown error code" in str(exc_info.value)

    def test_multiple_keys_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            TriageRequest({"500": "first error", "503": "second error"})
        assert "exactly one key" in str(exc_info.value)

    def test_empty_description_raises(self):
        with pytest.raises(ValidationError):
            TriageRequest({"500": "   "})


class TestTriageResponseSchema:
    def test_evidence_sources_defaults(self):
        r = TriageResponse(
            summary="ok",
            confidence_score=50,
            action_items=[],
            raw_error="500: test",
        )
        assert r.evidence_sources.knowledge_base is None
        assert r.evidence_sources.kubernetes == []

    def test_evidence_sources_in_openapi(self):
        schema = app.openapi()
        props = schema["components"]["schemas"]["TriageResponse"]["properties"]
        assert "evidence_sources" in props
        kb = schema["components"]["schemas"]["EvidenceSources"]["properties"]
        assert "knowledge_base" in kb
        assert "kubernetes" in kb
