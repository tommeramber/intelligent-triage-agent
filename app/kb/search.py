"""
Company troubleshooting knowledge-base lookup (domain logic).

Loaded from JSON on disk; matching is keyword + error-code based.
The stdio KB MCP server and the triage agent both call into this module.
"""

import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_DOCS_CACHE: list[dict] | None = None

KB_TOOL_NAME = "get_troubleshooting_docs"


def _load_docs() -> list[dict]:
    """Read and cache the troubleshooting docs JSON from disk."""
    global _DOCS_CACHE
    if _DOCS_CACHE is not None:
        return _DOCS_CACHE

    docs_path = Path(settings.docs_file_path)
    if not docs_path.exists():
        logger.warning("Docs file not found at %s — returning empty KB", docs_path)
        _DOCS_CACHE = []
        return _DOCS_CACHE

    with docs_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    _DOCS_CACHE = raw.get("entries", [])
    logger.info("Loaded %d troubleshooting doc entries from %s", len(_DOCS_CACHE), docs_path)
    return _DOCS_CACHE


def clear_docs_cache() -> None:
    """Reset the in-memory cache (tests only)."""
    global _DOCS_CACHE
    _DOCS_CACHE = None


def get_troubleshooting_docs(error_code: str, description: str = "") -> dict[str, Any]:
    """
    Look up relevant troubleshooting documentation for a given error.

    Matching strategy:
      1. Exact HTTP error-code match against the entry's `error_codes` list.
      2. Keyword overlap between `description` and the entry's `keywords` list.
    """
    docs = _load_docs()
    description_lower = description.lower()

    scored: list[tuple[int, dict]] = []

    for entry in docs:
        score = 0

        if error_code in entry.get("error_codes", []):
            score += 10

        for kw in entry.get("keywords", []):
            if kw in description_lower:
                score += 2

        if score > 0:
            score += entry.get("confidence_boost", 0)

        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_entries = [entry for _, entry in scored[:3]]

    logger.debug(
        "Docs lookup: code=%s matched %d/%d entries (top: %s)",
        error_code,
        len(scored),
        len(docs),
        [e["id"] for e in top_entries],
    )

    return {
        "matched_entries": top_entries,
        "total_found": len(scored),
    }


OPENAI_KB_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": KB_TOOL_NAME,
        "description": (
            "Retrieves relevant Company Troubleshooting Documentation for a given error. "
            "Call this tool first before forming any conclusions about the error. "
            "Returns a list of matching runbook entries with common causes and action items."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "error_code": {
                    "type": "string",
                    "description": (
                        "The HTTP status code or short error identifier from the log (e.g. '500', '403')."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Key terms from the error message to improve document relevance "
                        "(e.g. 'DB connection refused')."
                    ),
                },
            },
            "required": ["error_code"],
        },
    },
}
