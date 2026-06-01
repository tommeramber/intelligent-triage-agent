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

# Scoring weights (keyword overlap must contribute for API "useful" / "consulted" titles).
_CODE_MATCH_SCORE = 10
_KEYWORD_MATCH_SCORE = 2
_MAX_RESULTS = 3
_MAX_USEFUL = 1  # Primary grounding doc(s); consulted may list more


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


def _score_entry(entry: dict, error_code: str, description_lower: str) -> tuple[int, bool]:
    """Return (score, keyword_matched) for one KB entry."""
    score = 0
    keyword_matched = False

    if error_code in entry.get("error_codes", []):
        score += _CODE_MATCH_SCORE

    for kw in entry.get("keywords", []):
        if kw in description_lower:
            score += _KEYWORD_MATCH_SCORE
            keyword_matched = True

    if score > 0:
        score += entry.get("confidence_boost", 0)

    return score, keyword_matched


def get_troubleshooting_docs(error_code: str, description: str = "") -> dict[str, Any]:
    """
    Look up relevant troubleshooting documentation for a given error.

    Matching strategy:
      1. Exact HTTP error-code match against the entry's `error_codes` list.
      2. Keyword overlap between `description` and the entry's `keywords` list.

    Response semantics:
      - ``useful_entries`` / ``consulted_entries``: keyword-grounded only (not code-only).
      - ``matched_entries``: keyword hits first; if none, a single weak code-only fallback
        for the LLM (not promoted to consulted/useful titles on the API response).
      - ``keyword_match``: True when at least one entry matched description keywords.
    """
    docs = _load_docs()
    description_lower = description.lower()

    scored: list[tuple[int, bool, dict]] = []

    for entry in docs:
        score, keyword_matched = _score_entry(entry, error_code, description_lower)
        if score > 0:
            scored.append((score, keyword_matched, entry))

    scored.sort(key=lambda x: x[0], reverse=True)

    keyword_scored = [(s, e) for s, kw, e in scored if kw]
    code_only_scored = [(s, e) for s, kw, e in scored if not kw]

    consulted_entries = [e for _, e in keyword_scored[:_MAX_RESULTS]]
    useful_entries = [e for _, e in keyword_scored[:_MAX_USEFUL]]
    keyword_match = len(keyword_scored) > 0

    if keyword_scored:
        matched_entries = consulted_entries
    elif code_only_scored:
        matched_entries = [code_only_scored[0][1]]
    else:
        matched_entries = []

    logger.debug(
        "Docs lookup: code=%s keyword_match=%s matched %d/%d (useful: %s)",
        error_code,
        keyword_match,
        len(scored),
        len(docs),
        [e["id"] for e in useful_entries],
    )

    return {
        "matched_entries": matched_entries,
        "useful_entries": useful_entries,
        "consulted_entries": consulted_entries,
        "keyword_match": keyword_match,
        "code_only_fallback": not keyword_match and bool(code_only_scored),
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
