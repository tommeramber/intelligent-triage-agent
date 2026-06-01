"""
Application-side input safety checks (supplement to OpenAI Moderation).

OpenAI's Moderation API is the primary guard but often does not flag short,
ambiguous harm terms in SRE-shaped inputs (e.g. ``500: death``). This module
adds a small, deterministic word-boundary check for demo-visible violence terms.
"""

import re

from fastapi import HTTPException

# Sorted longest-first so regex alternation prefers longer matches.
_VIOLENCE_TERMS: tuple[str, ...] = (
    "terrorism",
    "terrorist",
    "massacre",
    "shooting",
    "violence",
    "violent",
    "murder",
    "killing",
    "suicide",
    "torture",
    "weapon",
    "stab",
    "kill",
    "bomb",
    "blood",
    "death",
    "gore",
    "gun",
)

_TERM_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in _VIOLENCE_TERMS) + r")\b",
    re.IGNORECASE,
)

_MODERATION_REJECTION_DETAIL = (
    "Input rejected by content moderation. Flagged categories: ['violence']"
)


def find_violence_terms(text: str) -> list[str]:
    """Return lowercased violence terms found in *text* (word boundaries)."""
    return [match.group(0).lower() for match in _TERM_PATTERN.finditer(text)]


def check_local_input_safety(text: str) -> None:
    """
    Raise HTTP 400 when *text* contains unambiguous violence-related terms.

    Intended as a fast pre-check before the OpenAI Moderation API call.
    """
    if find_violence_terms(text):
        raise HTTPException(status_code=400, detail=_MODERATION_REJECTION_DETAIL)
