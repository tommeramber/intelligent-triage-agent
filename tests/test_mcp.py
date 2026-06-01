"""Tests for MCP hub wiring and KB stdio server."""

import json

import pytest

from app.kb.search import KB_TOOL_NAME, clear_docs_cache, get_troubleshooting_docs
from app.mcp.hub import McpHub
from app.mcp.tool_schema import mcp_tool_to_openai
import mcp.types as mcp_types


class TestMcpToolSchema:
    def test_mcp_tool_to_openai_maps_fields(self):
        tool = mcp_types.Tool(
            name="get_troubleshooting_docs",
            description="Look up runbooks",
            inputSchema={
                "type": "object",
                "properties": {"error_code": {"type": "string"}},
                "required": ["error_code"],
            },
        )
        schema = mcp_tool_to_openai(tool)
        assert schema["type"] == "function"
        assert schema["function"]["name"] == KB_TOOL_NAME
        assert "error_code" in schema["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_kb_mcp_stdio_roundtrip():
    """KB MCP server returns the same payload as the domain module."""
    clear_docs_cache()
    hub = McpHub()
    await hub.start()
    try:
        if not hub.kb_available:
            pytest.skip("KB MCP not available in this environment")

        direct = get_troubleshooting_docs("500", "db connection refused")
        raw = await hub.call_tool(
            KB_TOOL_NAME,
            {"error_code": "500", "description": "db connection refused"},
        )
        via_mcp = json.loads(raw)
        assert via_mcp["total_found"] == direct["total_found"]
        assert via_mcp["matched_entries"][0]["id"] == direct["matched_entries"][0]["id"]
    finally:
        await hub.stop()


@pytest.mark.asyncio
async def test_classify_tool_kb_mcp_when_connected():
    hub = McpHub()
    await hub.start()
    try:
        if not hub.kb_available:
            pytest.skip("KB MCP not available in this environment")
        assert hub.classify_tool(KB_TOOL_NAME) == ("knowledge_base", "mcp")
    finally:
        await hub.stop()


@pytest.mark.asyncio
async def test_classify_tool_kb_fallback_when_mcp_disabled(monkeypatch):
    monkeypatch.setattr("app.mcp.hub.settings.mcp_kb_enabled", False)
    hub = McpHub()
    await hub.start()
    try:
        assert not hub.kb_available
        assert hub.classify_tool(KB_TOOL_NAME) == ("knowledge_base", "in_process_fallback")
    finally:
        await hub.stop()


@pytest.mark.asyncio
async def test_kb_mcp_fallback_when_server_disabled(monkeypatch):
    """In-process KB fallback when MCP subprocess is disabled."""
    monkeypatch.setattr("app.mcp.hub.settings.mcp_kb_enabled", False)

    hub = McpHub()
    await hub.start()
    try:
        raw = await hub.call_tool(
            KB_TOOL_NAME,
            {"error_code": "500", "description": "db connection refused"},
        )
        data = json.loads(raw)
        assert data["total_found"] > 0
    finally:
        await hub.stop()
