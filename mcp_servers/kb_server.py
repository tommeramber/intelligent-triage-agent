"""
Stdio MCP server for the company troubleshooting knowledge base.

Spawned by the app (same container/pod). Exposes one tool: get_troubleshooting_docs.
"""

from mcp.server.fastmcp import FastMCP

from app.kb.search import get_troubleshooting_docs

mcp = FastMCP("triage-kb")


@mcp.tool(name="get_troubleshooting_docs")
def get_troubleshooting_docs_tool(error_code: str, description: str = "") -> dict:
    """Look up runbook entries for an error code and optional description keywords."""
    return get_troubleshooting_docs(error_code=error_code, description=description)


if __name__ == "__main__":
    mcp.run(transport="stdio")
