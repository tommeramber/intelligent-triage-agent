"""Unit tests for application-side input safety checks."""

import pytest
from fastapi import HTTPException

from app.safety import check_local_input_safety, find_violence_terms


class TestFindViolenceTerms:
    def test_death_word_boundary(self):
        assert find_violence_terms("500: death") == ["death"]

    def test_deadline_not_matched(self):
        assert find_violence_terms("missed deadline for deployment") == []


class TestCheckLocalInputSafety:
    def test_blocks_death(self):
        with pytest.raises(HTTPException) as exc_info:
            check_local_input_safety("500: death")
        assert exc_info.value.status_code == 400
        assert "violence" in exc_info.value.detail

    def test_allows_normal_sre_text(self):
        check_local_input_safety("500: DB connection refused to postgres:5432")
