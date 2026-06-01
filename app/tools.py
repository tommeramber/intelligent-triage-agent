"""
OpenAI tool schemas and backward-compatible KB exports.

Domain lookup lives in app.kb.search; execution goes through the MCP hub in agent.py.
"""

from app.kb.search import KB_TOOL_NAME, OPENAI_KB_TOOL_SCHEMA, get_troubleshooting_docs

# Legacy alias used by tests and older imports.
TOOL_SCHEMA = OPENAI_KB_TOOL_SCHEMA

__all__ = [
    "KB_TOOL_NAME",
    "OPENAI_KB_TOOL_SCHEMA",
    "TOOL_SCHEMA",
    "get_troubleshooting_docs",
]
