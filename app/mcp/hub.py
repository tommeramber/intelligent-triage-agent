"""
MCP hub — manages stdio MCP servers (KB required, Kubernetes optional).

The triage agent talks only to the hub; servers run as child processes in the
same container (KB + hardened K8s MCP when enabled).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from app.config import settings
from app.kb.search import KB_TOOL_NAME, OPENAI_KB_TOOL_SCHEMA
from app.mcp.session import McpServerSpec, McpStdioSession
from app.mcp.tool_schema import mcp_tool_to_openai

logger = logging.getLogger(__name__)


class McpHub:
    """Registry of connected MCP servers and OpenAI tool schemas derived from them."""

    def __init__(self) -> None:
        self._kb = McpStdioSession(_kb_server_spec())
        self._k8s: McpStdioSession | None = None
        if settings.k8s_mcp_enabled:
            self._k8s = McpStdioSession(_k8s_server_spec())

    async def start(self) -> None:
        """Connect to configured MCP servers; failures are non-fatal except KB."""
        if settings.mcp_kb_enabled:
            ok = await self._kb.start()
            if not ok:
                logger.warning(
                    "KB MCP server failed to start (%s) — in-process KB fallback will be used",
                    self._kb.last_error,
                )
        if self._k8s is not None:
            await self._k8s.start()

    async def stop(self) -> None:
        await self._kb.stop()
        if self._k8s is not None:
            await self._k8s.stop()

    @property
    def kb_available(self) -> bool:
        return self._kb.available

    @property
    def k8s_available(self) -> bool:
        return self._k8s is not None and self._k8s.available

    @property
    def k8s_error(self) -> str | None:
        if self._k8s is None:
            return None
        return self._k8s.last_error

    def kb_tool_schemas(self) -> list[dict[str, Any]]:
        """Tools for iteration 0 (KB lookup is mandatory)."""
        if self._kb.available and self._kb.tools:
            return [mcp_tool_to_openai(t) for t in self._kb.tools if t.name == KB_TOOL_NAME]
        return [OPENAI_KB_TOOL_SCHEMA]

    def k8s_tool_schemas(self) -> list[dict[str, Any]]:
        """Optional cluster evidence tools (later iterations only)."""
        if not self.k8s_available or self._k8s is None:
            return []
        return [mcp_tool_to_openai(t) for t in self._k8s.tools]

    def all_tool_schemas(self) -> list[dict[str, Any]]:
        return self.kb_tool_schemas() + self.k8s_tool_schemas()

    def classify_tool(self, tool_name: str) -> tuple[str, str] | None:
        """
        Map a tool name to (channel, label) for response provenance.

        channel is knowledge_base or kubernetes; label is mcp, in_process_fallback,
        or the K8s tool name.
        """
        if tool_name == KB_TOOL_NAME:
            kb_channel = "mcp" if self.kb_available else "in_process_fallback"
            return ("knowledge_base", kb_channel)
        if self._kb.owns_tool(tool_name):
            return ("knowledge_base", "mcp")
        if self._k8s is not None and self._k8s.owns_tool(tool_name):
            return ("kubernetes", tool_name)
        return None

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        Route a tool call to the owning MCP server.

        Falls back to direct KB domain logic if the KB MCP subprocess is down
        (keeps triage working in constrained environments).
        """
        if self._kb.owns_tool(tool_name):
            return await self._kb.call_tool(tool_name, arguments)

        if self._k8s is not None and self._k8s.owns_tool(tool_name):
            from app.k8s.guard import enforce_k8s_tool_namespace

            guard_error = enforce_k8s_tool_namespace(tool_name, arguments)
            if guard_error:
                return json.dumps({"error": guard_error})
            return await self._k8s.call_tool(tool_name, arguments)

        if tool_name == KB_TOOL_NAME and not self._kb.available:
            from app.kb.search import get_troubleshooting_docs

            logger.warning("KB MCP down — using in-process KB fallback")
            result = get_troubleshooting_docs(
                error_code=arguments.get("error_code", ""),
                description=arguments.get("description", ""),
            )
            return json.dumps(result)

        return json.dumps({"error": f"tool '{tool_name}' is not available"})


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _kb_server_spec() -> McpServerSpec:
    root = _project_root()
    command = settings.mcp_kb_command
    if command in ("", "python3", "python"):
        command = sys.executable
    env = dict(os.environ)
    env["PYTHONPATH"] = settings.mcp_pythonpath or str(root)
    return McpServerSpec(
        name="triage-kb",
        command=command,
        args=settings.mcp_kb_args_list(),
        env=env,
        cwd=settings.mcp_workdir or str(root),
    )


def _k8s_server_spec() -> McpServerSpec:
    root = _project_root()
    command = settings.k8s_mcp_command
    if command in ("", "python3", "python"):
        command = sys.executable
    env = dict(os.environ)
    env.update(settings.k8s_mcp_env_map())
    env["PYTHONPATH"] = settings.mcp_pythonpath or str(root)
    env["K8S_ACCESS_LABEL_KEY"] = settings.k8s_access_label_key
    env["K8S_ACCESS_LABEL_VALUE"] = settings.k8s_access_label_value
    if settings.k8s_access_allowlist:
        env["K8S_ACCESS_ALLOWLIST"] = settings.k8s_access_allowlist
    return McpServerSpec(
        name="kubernetes",
        command=command,
        args=settings.k8s_mcp_args_list(),
        env=env,
        cwd=settings.mcp_workdir or str(root),
    )


def default_python_for_mcp() -> str:
    """Executable used to spawn the in-container KB MCP server."""
    return sys.executable
