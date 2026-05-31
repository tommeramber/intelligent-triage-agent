"""
Triage Agent — the reasoning core of the application.

Flow:
  1. Build an initial prompt from the raw error log.
  2. Send it to the LLM with the tool schema attached.
  3. If the LLM calls `get_troubleshooting_docs`, execute the tool and feed
     the result back. Repeat up to `agent_max_iterations` times.
  4. Once the LLM produces a plain (non-tool-call) message, parse the
     structured JSON from its content and return a TriageResponse.

Why OpenAI tool-calling instead of a framework (LangChain/LlamaIndex)?
  - Fewer dependencies → smaller image, faster startup, easier audit.
  - The tool-call loop is short enough (~20 lines) that a framework adds
    more ceremony than value.
  - Easy to swap the LLM provider: replace `openai.AsyncOpenAI` with any
    client that speaks the OpenAI Chat API (Azure, Ollama, Together, etc.).
"""

import json
import logging
import re
from typing import Any

import openai
from fastapi import HTTPException

from app.config import settings
from app.models import TriageResponse
from app.tools import TOOL_SCHEMA, get_troubleshooting_docs

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────
# Written as a constant so it is easy to version-control and tweak without
# hunting through business logic.
SYSTEM_PROMPT = """
You are an expert SRE (Site Reliability Engineer) first-responder agent.
Your job is to analyse raw error logs, consult the company troubleshooting
knowledge base, and return a precise, actionable triage report.

Rules:
- ALWAYS call get_troubleshooting_docs before drawing conclusions.
  (This is also enforced structurally: tool_choice="required" on the first
  iteration guarantees you cannot skip the knowledge-base lookup. On subsequent
  iterations tool_choice is "auto" so you can answer freely once you have the
  results.)
- Be concise but technically precise.
- Do not invent action items that are not grounded in the docs or the error.
- Confidence score (0-100) must reflect how certain you are given the available information.
  Use lower scores when the error is ambiguous or docs coverage is thin.

You MUST respond with ONLY a valid JSON object in this exact schema — no prose, no markdown fences:
{
  "summary": "<one sentence describing the root problem>",
  "confidence_score": <integer 0-100>,
  "action_items": ["<step 1>", "<step 2>", ...],
  "docs_consulted": ["<doc title 1>", ...]
}
""".strip()


def _build_user_message(error_code: str, description: str) -> str:
    """Format the user-facing part of the initial prompt."""
    return f"Error log:\n{error_code}: {description}"


def _dispatch_tool_call(tool_name: str, arguments_json: str) -> str:
    """
    Execute a tool requested by the LLM.

    Returns the tool result as a JSON string so it can be appended to the
    message history verbatim.
    """
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse tool arguments: %s", exc)
        return json.dumps({"error": "invalid arguments from LLM"})

    if tool_name == "get_troubleshooting_docs":
        result = get_troubleshooting_docs(
            error_code=args.get("error_code", ""),
            description=args.get("description", ""),
        )
        return json.dumps(result)

    # Unknown tool — return an error so the LLM can recover gracefully.
    logger.warning("LLM requested unknown tool: %s", tool_name)
    return json.dumps({"error": f"tool '{tool_name}' is not available"})


def _parse_llm_json(content: str) -> dict[str, Any]:
    """
    Extract the JSON object from the LLM's final response.

    The LLM occasionally wraps output in markdown code fences (```json … ```)
    despite the system prompt asking it not to. This handles that gracefully.
    """
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", content).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Last-resort: try to find the first {...} block in the text
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Could not parse JSON from LLM response: {content[:200]}")


async def _check_moderation(client: openai.AsyncOpenAI, text: str) -> None:
    """
    Pre-flight content moderation using OpenAI's free Moderation API.

    Sends the input description to the /v1/moderations endpoint before
    any agent reasoning begins. This catches violent, hateful, or prompt-injection
    adjacent content early and prevents it from reaching the LLM.

    Trade-off: adds ~100-300ms latency per request (one extra API round-trip).
    This is an explicit, accepted cost for the safety guarantee it provides.

    Raises:
        HTTPException(400): if the content is flagged by any moderation category.
    """
    response = await client.moderations.create(input=text)
    result = response.results[0]
    if result.flagged:
        # Surface the triggered categories to help legitimate users fix their input
        flagged_categories = [
            cat for cat, flagged in result.categories.model_dump().items() if flagged
        ]
        raise HTTPException(
            status_code=400,
            detail=f"Input rejected by content moderation. Flagged categories: {flagged_categories}",
        )


async def run_triage(error_code: str, description: str, client: openai.AsyncOpenAI) -> TriageResponse:
    """
    Entry point for the triage agent.

    Args:
        error_code:  HTTP status code or short error identifier (e.g. "500").
        description: Error description / message from the caller.
        client:      Shared AsyncOpenAI client (created once via FastAPI lifespan).

    Returns:
        A TriageResponse with summary, confidence_score, action_items, and
        the list of doc titles the agent consulted.

    Raises:
        openai.OpenAIError: on API-level failures (auth, rate limit, etc.)
        ValueError:         if the LLM response cannot be parsed.
    """
    # Run moderation check before any LLM reasoning.
    # Note: adds ~100-300ms latency — an accepted cost for content safety.
    await _check_moderation(client, f"{error_code}: {description}")

    # Start with system + user messages
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(error_code, description)},
    ]

    docs_consulted: list[str] = []

    for iteration in range(settings.agent_max_iterations):
        logger.debug("Agent iteration %d/%d", iteration + 1, settings.agent_max_iterations)

        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            tools=[TOOL_SCHEMA],
            # "required" forces the LLM to call a tool on this turn — it cannot skip straight
            # to a conclusion without consulting the knowledge base first.
            # Only switch back to "auto" if you add tools that are optional to call.
            tool_choice="required" if iteration == 0 else "auto",
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )

        choice = response.choices[0]
        assistant_msg = choice.message

        # Append the assistant's response to the conversation history
        messages.append(assistant_msg.model_dump(exclude_unset=True))

        # ── Case 1: LLM wants to call a tool ──────────────────────────────────
        if choice.finish_reason == "tool_calls" and assistant_msg.tool_calls:
            for tool_call in assistant_msg.tool_calls:
                tool_name = tool_call.function.name
                logger.info("Agent calling tool: %s", tool_name)

                result_str = _dispatch_tool_call(tool_name, tool_call.function.arguments)

                # Feed the tool result back into the conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                })

                # Track which doc entries were surfaced (for the response)
                try:
                    result_data = json.loads(result_str)
                    entries = result_data.get("matched_entries", [])
                    logger.info("Tool returned %d matched entries", len(entries))
                    for entry in entries:
                        title = entry.get("title", entry.get("id", "unknown"))
                        if title not in docs_consulted:
                            docs_consulted.append(title)
                except (json.JSONDecodeError, AttributeError) as e:
                    logger.warning("Failed to parse tool result for docs_consulted: %s", e)

            continue  # go back to the top to send the tool results to the LLM

        # ── Case 2: LLM produced a final answer ───────────────────────────────
        if choice.finish_reason in ("stop", "length"):
            content = assistant_msg.content or ""
            parsed = _parse_llm_json(content)

            return TriageResponse(
                summary=parsed.get("summary", "No summary provided."),
                confidence_score=int(parsed.get("confidence_score", 50)),
                action_items=parsed.get("action_items", []),
                docs_consulted=docs_consulted,  # use tool-sourced list; LLM's claim may be hallucinated
                raw_error=f"{error_code}: {description}",
            )

        # Unexpected finish reason — treat as error
        logger.error("Unexpected finish_reason: %s", choice.finish_reason)
        break

    # Fallback if we exhausted iterations or hit an unexpected state
    logger.warning("Agent exhausted iterations without a clean answer.")
    return TriageResponse(
        summary="Agent could not determine root cause within the allowed reasoning steps.",
        confidence_score=0,
        action_items=["Escalate to Senior Dev — automated triage was inconclusive."],
        docs_consulted=docs_consulted,
        raw_error=f"{error_code}: {description}",
    )
