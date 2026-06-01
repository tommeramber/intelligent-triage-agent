"""Convert MCP tool definitions to OpenAI function-calling schemas."""

from typing import Any

import mcp.types as mcp_types


def mcp_tool_to_openai(tool: mcp_types.Tool) -> dict[str, Any]:
    """Map an MCP Tool descriptor to an OpenAI chat-completions tool entry."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
        },
    }
