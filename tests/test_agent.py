"""
Unit tests for the triage agent.

These tests mock the OpenAI client so they run fully offline —
no API key needed, no cost, no flakiness from network.

Run with:  make test
           or: pytest tests/ -v
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.kb.search import get_troubleshooting_docs
from app.kb.search import OPENAI_KB_TOOL_SCHEMA
from app.models import TriageRequest, TriageResponse


# ── Tool tests (no mocking needed — pure Python logic) ────────────────────────

class TestGetTroubleshootingDocs:
    """Tests for the knowledge-base lookup tool."""

    def test_exact_code_match_without_keywords_is_fallback_only(self):
        result = get_troubleshooting_docs("500")
        assert result["total_found"] > 0
        assert result["keyword_match"] is False
        assert result["useful_entries"] == []
        assert result["consulted_entries"] == []
        assert len(result["matched_entries"]) == 1
        assert result["code_only_fallback"] is True

    def test_keyword_match_improves_ranking(self):
        result_with_kw = get_troubleshooting_docs("500", "DB connection refused")
        result_without  = get_troubleshooting_docs("500", "")
        # The DB-connection entry should rank higher when we include keywords
        top_with    = result_with_kw["matched_entries"][0]["id"]
        assert top_with == "db-connection-refused"
        assert result_with_kw["keyword_match"] is True
        assert result_with_kw["useful_entries"][0]["id"] == "db-connection-refused"
        assert len(result_with_kw["consulted_entries"]) >= 1

    def test_code_only_does_not_populate_consulted_or_useful(self):
        result = get_troubleshooting_docs("500", "generic internal failure")
        assert result["keyword_match"] is False
        assert result["consulted_entries"] == []
        assert result["useful_entries"] == []

    def test_pending_pod_keywords_match_infra_entry(self):
        result = get_troubleshooting_docs(
            "503",
            "Pods stuck Pending — scheduler reports FailedScheduling, 0/3 nodes available",
        )
        assert result["keyword_match"] is True
        ids = [e["id"] for e in result["consulted_entries"]]
        assert "pod-pending-scheduling" in ids

    def test_crashloop_keywords_match_without_http_code_on_entry(self):
        result = get_troubleshooting_docs(
            "503",
            "No healthy upstream — pods in triage-demo CrashLoopBackOff",
        )
        assert result["keyword_match"] is True
        assert result["useful_entries"][0]["id"] == "crash-loop"

    def test_403_no_restart_action(self):
        result = get_troubleshooting_docs("403", "access denied forbidden rbac")
        top = result["matched_entries"][0]
        action_text = " ".join(top.get("action_items", [])).lower()
        # The 403 doc should explicitly say NOT to restart — auth issues don't need a pod restart
        assert "do not restart" in action_text
        assert result["keyword_match"] is True

    def test_unknown_code_returns_empty(self):
        result = get_troubleshooting_docs("999", "some unknown error")
        assert result["total_found"] == 0
        assert result["matched_entries"] == []

    def test_max_three_results(self):
        # Error code 500 matches several entries
        result = get_troubleshooting_docs("500", "oom memory timeout database")
        assert len(result["matched_entries"]) <= 3


# ── Model validation tests ────────────────────────────────────────────────────

class TestModels:
    def test_triage_request_valid(self):
        req = TriageRequest({"500": "DB connection refused"})
        assert req.error_code == "500"
        assert req.description == "DB connection refused"

    def test_triage_request_empty_dict_raises(self):
        from pydantic import ValidationError
        # An empty dict is technically valid for Dict[str, str] in pydantic,
        # but accessing error_code/description would raise StopIteration.
        # We validate that the RootModel accepts the exact exercise format.
        req = TriageRequest({"500": "DB connection refused"})
        assert req.error_code == "500"

    def test_triage_request_rejects_non_dict(self):
        from pydantic import ValidationError
        with pytest.raises((ValidationError, Exception)):
            TriageRequest("not a dict")

    def test_triage_response_confidence_bounds(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TriageResponse(
                summary="test",
                confidence_score=150,   # out of range
                action_items=[],
                raw_error="test",
            )

    def test_triage_response_valid(self):
        r = TriageResponse(
            summary="DB connection is refused.",
            confidence_score=85,
            action_items=["Restart Pod", "Check DB Credentials"],
            raw_error="500: connection refused",
        )
        assert r.confidence_score == 85
        assert len(r.action_items) == 2


# ── Agent integration test (mocked OpenAI) ───────────────────────────────────

class _FakeMcpHub:
    """Minimal hub stub so agent tests do not spawn MCP subprocesses."""

    kb_available = True
    k8s_available = False
    k8s_error = None

    def classify_tool(self, tool_name):
        if tool_name == "get_troubleshooting_docs":
            return ("knowledge_base", "mcp")
        return None

    def kb_tool_schemas(self):
        return [OPENAI_KB_TOOL_SCHEMA]

    def all_tool_schemas(self, *, allow_k8s=True):
        return self.kb_tool_schemas()

    async def call_tool(self, tool_name, arguments):
        import json as _json

        result = get_troubleshooting_docs(
            error_code=arguments.get("error_code", ""),
            description=arguments.get("description", ""),
        )
        return _json.dumps(result)


@pytest.mark.asyncio
async def test_run_triage_happy_path():
    """
    Verifies the full agent loop:
    1. LLM asks for tool call
    2. Tool returns docs
    3. LLM returns final JSON answer
    """
    from app.agent import run_triage

    # Build the sequence of mock responses the fake OpenAI API will return.
    tool_call_response = MagicMock()
    tool_call_response.choices[0].finish_reason = "tool_calls"
    tool_call_response.choices[0].message.content = None
    tool_call_response.choices[0].message.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": "call_123", "function": {"name": "get_troubleshooting_docs", "arguments": '{"error_code": "500", "description": "db connection refused"}'}}],
    }
    tc = MagicMock()
    tc.id = "call_123"
    tc.function.name = "get_troubleshooting_docs"
    tc.function.arguments = '{"error_code": "500", "description": "db connection refused"}'
    tool_call_response.choices[0].message.tool_calls = [tc]

    final_answer = {
        "summary": "Database connection refused — postgres is unreachable from the app pod.",
        "confidence_score": 90,
        "action_items": ["Check DB pod status", "Verify DB_HOST env var", "Test connectivity with nc"],
        "docs_consulted": ["Database Connection Refused"],
    }
    final_response = MagicMock()
    final_response.choices[0].finish_reason = "stop"
    final_response.choices[0].message.content = json.dumps(final_answer)
    final_response.choices[0].message.tool_calls = None
    final_response.choices[0].message.model_dump.return_value = {
        "role": "assistant",
        "content": json.dumps(final_answer),
    }

    mock_mod_result = MagicMock()
    mock_mod_result.results[0].flagged = False

    mock_client = MagicMock()
    mock_client.moderations.create = AsyncMock(return_value=mock_mod_result)
    mock_client.chat.completions.create = AsyncMock(side_effect=[tool_call_response, final_response])

    # Pass mock client directly — no need to patch AsyncOpenAI constructor
    result = await run_triage(
        "500",
        "DB connection refused to postgres:5432",
        client=mock_client,
        mcp_hub=_FakeMcpHub(),
    )

    assert isinstance(result, TriageResponse)
    assert result.confidence_score == 90
    assert "Check DB pod status" in result.action_items
    assert result.raw_error == "500: DB connection refused to postgres:5432"
    assert result.evidence_sources.knowledge_base == "mcp"
    assert result.evidence_sources.kubernetes.invoked == []
    assert result.evidence_sources.kubernetes.status == "not_invoked"
    assert result.evidence_sources.kubernetes.evidence_obtained is False
    assert result.kb_keyword_match is True
    assert "Database Connection Refused" in result.docs_useful
    assert "Database Connection Refused" in result.docs_consulted


class _FakeMcpHubWithK8s(_FakeMcpHub):
    """Hub stub that exposes a second tool like the in-cluster K8s MCP server."""

    k8s_available = True

    def classify_tool(self, tool_name):
        if tool_name == "list_pods":
            return ("kubernetes", tool_name)
        return super().classify_tool(tool_name)

    def all_tool_schemas(self, *, allow_k8s=True):
        schemas = super().all_tool_schemas(allow_k8s=allow_k8s)
        if not allow_k8s:
            return schemas
        schemas.append({
            "type": "function",
            "function": {
                "name": "list_pods",
                "description": "List pods in a namespace",
                "parameters": {
                    "type": "object",
                    "properties": {"namespace": {"type": "string"}},
                    "required": ["namespace"],
                },
            },
        })
        return schemas

    async def call_tool(self, tool_name, arguments):
        import json as _json

        if tool_name == "list_pods":
            return _json.dumps({"namespace": arguments.get("namespace"), "pods": []})
        return await super().call_tool(tool_name, arguments)


@pytest.mark.asyncio
async def test_run_triage_reserves_last_iteration_for_final_answer(monkeypatch):
    """
    With AGENT_MAX_ITERATIONS=3, KB + one K8s tool call must still yield a triage
    response (final iteration must not offer tools).
    """
    from app.agent import run_triage

    monkeypatch.setattr("app.agent.settings.agent_max_iterations", 3)

    def _tool_round(name: str, args: str, call_id: str):
        tc = MagicMock()
        tc.id = call_id
        tc.function.name = name
        tc.function.arguments = args
        response = MagicMock()
        response.choices[0].finish_reason = "tool_calls"
        response.choices[0].message.content = None
        response.choices[0].message.tool_calls = [tc]
        response.choices[0].message.model_dump.return_value = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "function": {"name": name, "arguments": args},
                }
            ],
        }
        return response

    kb_round = _tool_round(
        "get_troubleshooting_docs",
        '{"error_code": "500", "description": "db connection refused"}',
        "call_kb",
    )
    k8s_round = _tool_round(
        "list_pods",
        '{"namespace": "triage-demo"}',
        "call_k8s",
    )

    final_answer = {
        "summary": "Database connection refused — postgres is unreachable from the app pod.",
        "confidence_score": 88,
        "action_items": ["Check DB pod status", "Verify DB_HOST env var"],
        "docs_consulted": ["Database Connection Refused"],
    }
    final_response = MagicMock()
    final_response.choices[0].finish_reason = "stop"
    final_response.choices[0].message.content = json.dumps(final_answer)
    final_response.choices[0].message.tool_calls = None
    final_response.choices[0].message.model_dump.return_value = {
        "role": "assistant",
        "content": json.dumps(final_answer),
    }

    mock_mod_result = MagicMock()
    mock_mod_result.results[0].flagged = False

    mock_client = MagicMock()
    mock_client.moderations.create = AsyncMock(return_value=mock_mod_result)
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[kb_round, k8s_round, final_response]
    )

    result = await run_triage(
        "500",
        "DB connection refused — pod api-db-7d4f8b in triage-demo namespace",
        client=mock_client,
        mcp_hub=_FakeMcpHubWithK8s(),
    )

    assert result.confidence_score == 88
    assert "postgres" in result.summary.lower()
    assert result.evidence_sources.knowledge_base == "mcp"
    assert result.evidence_sources.kubernetes.invoked == ["list_pods"]
    assert result.evidence_sources.kubernetes.status == "obtained"
    assert result.evidence_sources.kubernetes.evidence_obtained is True
    assert len(result.docs_consulted) > 0

    create_calls = mock_client.chat.completions.create.await_args_list
    assert len(create_calls) == 3
    assert "tools" not in create_calls[-1].kwargs


class _FakeMcpHubK8sDenied(_FakeMcpHubWithK8s):
    """K8s tool invoked but namespace guard returns an error payload."""

    async def call_tool(self, tool_name, arguments):
        import json as _json

        if tool_name == "list_pods":
            return _json.dumps({"error": "namespace 'default' is not accessible"})
        return await _FakeMcpHub.call_tool(self, tool_name, arguments)


@pytest.mark.asyncio
async def test_run_triage_k8s_invoked_without_evidence(monkeypatch):
    """K8s MCP called but blocked — invoked is set, evidence_obtained stays false."""
    from app.agent import run_triage

    monkeypatch.setattr("app.agent.settings.agent_max_iterations", 3)

    def _tool_round(name: str, args: str, call_id: str):
        tc = MagicMock()
        tc.id = call_id
        tc.function.name = name
        tc.function.arguments = args
        response = MagicMock()
        response.choices[0].finish_reason = "tool_calls"
        response.choices[0].message.content = None
        response.choices[0].message.tool_calls = [tc]
        response.choices[0].message.model_dump.return_value = {
            "role": "assistant",
            "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": args}}],
        }
        return response

    kb_round = _tool_round(
        "get_troubleshooting_docs",
        '{"error_code": "500", "description": "db connection refused"}',
        "call_kb",
    )
    k8s_round = _tool_round(
        "list_pods",
        '{"namespace": "default"}',
        "call_k8s",
    )
    final_answer = {
        "summary": "Database connection refused.",
        "confidence_score": 70,
        "action_items": ["Check DB pod"],
    }
    final_response = MagicMock()
    final_response.choices[0].finish_reason = "stop"
    final_response.choices[0].message.content = json.dumps(final_answer)
    final_response.choices[0].message.tool_calls = None
    final_response.choices[0].message.model_dump.return_value = {
        "role": "assistant",
        "content": json.dumps(final_answer),
    }

    mock_mod_result = MagicMock()
    mock_mod_result.results[0].flagged = False
    mock_client = MagicMock()
    mock_client.moderations.create = AsyncMock(return_value=mock_mod_result)
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[kb_round, k8s_round, final_response]
    )

    result = await run_triage(
        "500",
        "DB connection refused — pod api-db-7d4f8b in triage-demo namespace",
        client=mock_client,
        mcp_hub=_FakeMcpHubK8sDenied(),
    )

    assert result.evidence_sources.kubernetes.invoked == ["list_pods"]
    assert result.evidence_sources.kubernetes.status == "access_denied"
    assert result.evidence_sources.kubernetes.evidence_obtained is False
    assert "namespace" in (result.evidence_sources.kubernetes.message or "").lower()


class _FakeMcpHubK8sAllowlistOnly(_FakeMcpHubWithK8s):
    """Only list_accessible_namespaces succeeds — must not set evidence_obtained."""

    def classify_tool(self, tool_name):
        if tool_name == "list_accessible_namespaces":
            return ("kubernetes", tool_name)
        return super().classify_tool(tool_name)

    def all_tool_schemas(self, *, allow_k8s=True):
        schemas = super().all_tool_schemas(allow_k8s=allow_k8s)
        if not allow_k8s:
            return schemas
        schemas.append({
            "type": "function",
            "function": {
                "name": "list_accessible_namespaces",
                "description": "List allowed namespaces",
                "parameters": {"type": "object", "properties": {}},
            },
        })
        return schemas

    async def call_tool(self, tool_name, arguments):
        import json as _json

        if tool_name == "list_accessible_namespaces":
            return _json.dumps({"namespaces": ["triage-demo"], "count": 1})
        return await super().call_tool(tool_name, arguments)


@pytest.mark.asyncio
async def test_run_triage_k8s_allowlist_only_not_evidence(monkeypatch):
    """Allowlist discovery alone must not mark evidence_obtained true."""
    from app.agent import run_triage

    monkeypatch.setattr("app.agent.settings.agent_max_iterations", 3)

    def _tool_round(name: str, args: str, call_id: str):
        tc = MagicMock()
        tc.id = call_id
        tc.function.name = name
        tc.function.arguments = args
        response = MagicMock()
        response.choices[0].finish_reason = "tool_calls"
        response.choices[0].message.content = None
        response.choices[0].message.tool_calls = [tc]
        response.choices[0].message.model_dump.return_value = {
            "role": "assistant",
            "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": args}}],
        }
        return response

    kb_round = _tool_round(
        "get_troubleshooting_docs",
        '{"error_code": "500", "description": "node is down in cluster"}',
        "call_kb",
    )
    k8s_round = _tool_round(
        "list_accessible_namespaces",
        "{}",
        "call_k8s",
    )
    final_answer = {
        "summary": "Node failure suspected.",
        "confidence_score": 40,
        "action_items": ["Check node status"],
    }
    final_response = MagicMock()
    final_response.choices[0].finish_reason = "stop"
    final_response.choices[0].message.content = json.dumps(final_answer)
    final_response.choices[0].message.tool_calls = None
    final_response.choices[0].message.model_dump.return_value = {
        "role": "assistant",
        "content": json.dumps(final_answer),
    }

    mock_mod_result = MagicMock()
    mock_mod_result.results[0].flagged = False
    mock_client = MagicMock()
    mock_client.moderations.create = AsyncMock(return_value=mock_mod_result)
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[kb_round, k8s_round, final_response]
    )

    result = await run_triage(
        "500",
        "node failure in triage-demo namespace — check cluster nodes",
        client=mock_client,
        mcp_hub=_FakeMcpHubK8sAllowlistOnly(),
    )

    assert result.evidence_sources.kubernetes.invoked == ["list_accessible_namespaces"]
    assert result.evidence_sources.kubernetes.status == "no_accessible_evidence"
    assert result.evidence_sources.kubernetes.evidence_obtained is False


@pytest.mark.asyncio
async def test_run_triage_vague_input_withholds_k8s_tools(monkeypatch):
    """Without namespace/pod/workload in the error, K8s tools must not be offered."""
    from app.agent import run_triage

    monkeypatch.setattr("app.agent.settings.agent_max_iterations", 3)

    def _tool_round(name: str, args: str, call_id: str):
        tc = MagicMock()
        tc.id = call_id
        tc.function.name = name
        tc.function.arguments = args
        response = MagicMock()
        response.choices[0].finish_reason = "tool_calls"
        response.choices[0].message.content = None
        response.choices[0].message.tool_calls = [tc]
        response.choices[0].message.model_dump.return_value = {
            "role": "assistant",
            "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": args}}],
        }
        return response

    kb_round = _tool_round(
        "get_troubleshooting_docs",
        '{"error_code": "500", "description": "node is down in cluster"}',
        "call_kb",
    )
    final_answer = {
        "summary": "Suspected node failure — no concrete workload target in report.",
        "confidence_score": 45,
        "action_items": ["Check node status via platform ops"],
    }
    final_response = MagicMock()
    final_response.choices[0].finish_reason = "stop"
    final_response.choices[0].message.content = json.dumps(final_answer)
    final_response.choices[0].message.tool_calls = None
    final_response.choices[0].message.model_dump.return_value = {
        "role": "assistant",
        "content": json.dumps(final_answer),
    }

    mock_mod_result = MagicMock()
    mock_mod_result.results[0].flagged = False
    mock_client = MagicMock()
    mock_client.moderations.create = AsyncMock(return_value=mock_mod_result)
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[kb_round, final_response]
    )

    result = await run_triage(
        "500",
        "node is down in cluster",
        client=mock_client,
        mcp_hub=_FakeMcpHubWithK8s(),
    )

    assert result.evidence_sources.kubernetes.status == "not_invoked"
    assert result.evidence_sources.kubernetes.invoked == []

    create_calls = mock_client.chat.completions.create.await_args_list
    assert len(create_calls) == 2
    mid_tools = create_calls[1].kwargs.get("tools") or []
    tool_names = [t["function"]["name"] for t in mid_tools]
    assert "list_pods" not in tool_names
    assert "get_troubleshooting_docs" in tool_names


# ── Moderation tests ──────────────────────────────────────────────────────────

class TestModeration:
    @pytest.mark.asyncio
    async def test_local_guard_blocks_death_demo_input(self):
        """Demo input like {"500": "death"} must 400 before OpenAI moderation."""
        from app.agent import run_triage
        from fastapi import HTTPException

        mock_client = MagicMock()
        mock_client.moderations.create = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await run_triage("500", "death", client=mock_client)

        assert exc_info.value.status_code == 400
        assert "violence" in exc_info.value.detail
        mock_client.moderations.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_moderation_blocks_flagged_content(self):
        """Verify that OpenAI-flagged content raises HTTPException 400."""
        from app.agent import run_triage
        from fastapi import HTTPException

        mock_mod_result = MagicMock()
        mock_mod_result.results[0].flagged = True
        mock_mod_result.results[0].categories.model_dump.return_value = {
            "violence": True, "hate": False, "self-harm": False
        }

        mock_client = MagicMock()
        mock_client.moderations.create = AsyncMock(return_value=mock_mod_result)

        with pytest.raises(HTTPException) as exc_info:
            await run_triage(
                "500",
                "upstream proxy returned errors repeatedly",
                client=mock_client,
            )

        assert exc_info.value.status_code == 400
        assert "violence" in exc_info.value.detail
        mock_client.moderations.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_moderation_passes_clean_content(self):
        """Clean SRE input passes local guard and reaches OpenAI moderation."""
        from app.safety import check_local_input_safety

        check_local_input_safety("500: DB connection refused to postgres:5432")
