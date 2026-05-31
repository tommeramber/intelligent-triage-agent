"""
Mock tool: get_troubleshooting_docs

This module simulates a "Company Troubleshooting Knowledge Base" lookup.
In a real system this would call an internal wiki API, a vector DB (RAG),
or a Confluence/Notion integration. Here we load from a local JSON file so
the whole stack works offline and without external dependencies beyond the LLM.

The function is registered with the LLM as an OpenAI-style tool (function call),
so the agent can autonomously decide when and how to invoke it.

Design choice — why JSON file over alternatives:
  ┌──────────────────────┬────────────────────────────────────────────────────┐
  │ Option               │ Trade-off                                          │
  ├──────────────────────┼────────────────────────────────────────────────────┤
  │ Hardcoded dict       │ Simplest, but requires code change to update docs  │
  │ JSON file (chosen)   │ Config-driven; swap docs without touching code     │
  │ SQLite               │ Queryable, but overkill for a mock KB              │
  │ Separate HTTP svc    │ Most realistic, but adds a second K8s deployment   │
  └──────────────────────┴────────────────────────────────────────────────────┘
"""

import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Load the docs once at import time. If the file changes at runtime (e.g. a
# ConfigMap update), you'd need to add inotify watching — acceptable Phase-2 work.
_DOCS_CACHE: list[dict] | None = None


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


def get_troubleshooting_docs(error_code: str, description: str = "") -> dict[str, Any]:
    """
    Look up relevant troubleshooting documentation for a given error.

    Matching strategy (simple, no external deps):
      1. Exact HTTP error-code match against the entry's `error_codes` list.
      2. Keyword overlap between `description` and the entry's `keywords` list.
    Entries are ranked by the number of matched keywords + code hit.

    Args:
        error_code:  The HTTP status code or short error identifier (e.g. "500", "403").
        description: Free-text from the error log to improve relevance.

    Returns:
        A dict with:
          - matched_entries: list of the top-3 most relevant doc entries
          - total_found:     how many entries matched at all
    """
    docs = _load_docs()
    description_lower = description.lower()

    scored: list[tuple[int, dict]] = []

    for entry in docs:
        score = 0

        # +10 for every error_code match
        if error_code in entry.get("error_codes", []):
            score += 10

        # +2 for each keyword found in the description
        for kw in entry.get("keywords", []):
            if kw in description_lower:
                score += 2

        # Add the entry's own confidence boost only when there's already a match
        if score > 0:
            score += entry.get("confidence_boost", 0)

        if score > 0:
            scored.append((score, entry))

    # Sort descending by score, take top 3
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


# ── OpenAI tool schema ────────────────────────────────────────────────────────
# This dict is passed directly to the OpenAI chat-completions API as a "tool".
# The LLM reads the description/parameters to decide when and how to call it.

TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_troubleshooting_docs",
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
                    "description": "The HTTP status code or short error identifier extracted from the log (e.g. '500', '403').",
                },
                "description": {
                    "type": "string",
                    "description": "Key terms from the error message to improve document relevance (e.g. 'DB connection refused').",
                },
            },
            "required": ["error_code"],
        },
    },
}
