"""Long-lived stdio MCP client session for one server process."""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import mcp.types as mcp_types
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

logger = logging.getLogger(__name__)


@dataclass
class McpServerSpec:
    """How to spawn a stdio MCP server subprocess."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


class McpStdioSession:
    """Wraps MCP ClientSession over a stdio transport with explicit start/stop."""

    def __init__(self, spec: McpServerSpec) -> None:
        self.spec = spec
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._tools: list[mcp_types.Tool] = []
        self._tool_names: set[str] = set()
        self.available = False
        self.last_error: str | None = None

    async def start(self) -> bool:
        """Spawn the server and initialize the MCP session. Returns False on failure."""
        try:
            params = StdioServerParameters(
                command=self.spec.command,
                args=self.spec.args,
                env=self.spec.env,
                cwd=self.spec.cwd,
            )
            read_stream, write_stream = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            tools_result = await session.list_tools()
            self._session = session
            self._tools = list(tools_result.tools)
            self._tool_names = {t.name for t in self._tools}
            self.available = True
            logger.info(
                "MCP server %r connected (%d tools: %s)",
                self.spec.name,
                len(self._tools),
                sorted(self._tool_names)[:8],
            )
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.available = False
            logger.warning("MCP server %r unavailable: %s", self.spec.name, exc)
            await self.stop()
            return False

    async def stop(self) -> None:
        """Tear down the subprocess and session."""
        await self._stack.aclose()
        self._session = None
        self._tools = []
        self._tool_names = set()
        self.available = False

    @property
    def tools(self) -> list[mcp_types.Tool]:
        return list(self._tools)

    def owns_tool(self, name: str) -> bool:
        return name in self._tool_names

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Invoke a tool and return a JSON string for the OpenAI tool message."""
        if not self._session or not self.available:
            return json.dumps({"error": f"MCP server {self.spec.name!r} is not connected"})

        result = await self._session.call_tool(name, arguments or {})
        if result.isError:
            return _format_tool_error(result)

        payload = _extract_tool_payload(result)
        if isinstance(payload, (dict, list)):
            return json.dumps(payload)
        return json.dumps({"result": payload})


def _extract_tool_payload(result: mcp_types.CallToolResult) -> Any:
    if result.structuredContent is not None:
        return result.structuredContent

    texts: list[str] = []
    for block in result.content:
        if isinstance(block, mcp_types.TextContent):
            texts.append(block.text)
    combined = "\n".join(texts).strip()
    if not combined:
        return {}
    try:
        return json.loads(combined)
    except json.JSONDecodeError:
        return combined


def _format_tool_error(result: mcp_types.CallToolResult) -> str:
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, mcp_types.TextContent):
            parts.append(block.text)
    message = "\n".join(parts).strip() or "tool call failed"
    return json.dumps({"error": message})
