"""
Triage Agent — the reasoning core of the application.

Flow:
  1. Build an initial prompt from the raw error log.
  2. Send it to the LLM with KB tool schema attached (via MCP).
  3. If the LLM calls tools, execute them through McpHub and feed results back.
  4. Later iterations may use optional Kubernetes MCP tools when connected.
  5. Once the LLM produces a plain (non-tool-call) message, parse JSON → TriageResponse.
"""

import json
import logging
import re
from typing import Any

import openai
from fastapi import HTTPException

from app.config import settings
from app.kb.search import KB_TOOL_NAME
from app.mcp.hub import McpHub
from app.models import EvidenceSources, TriageResponse

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an expert SRE (Site Reliability Engineer) first-responder agent.
Your job is to analyse raw error logs, consult the company troubleshooting
knowledge base, and return a precise, actionable triage report.

Rules:
- ALWAYS call get_troubleshooting_docs before drawing conclusions.
  (This is enforced on the first iteration: you must run the KB tool before answering.)
- When Kubernetes MCP tools are available, you MAY use them after the KB lookup
  to gather read-only cluster evidence (pods, logs, events). Do not mutate the cluster.
- Be concise but technically precise.
- Do not invent action items that are not grounded in the docs, cluster evidence, or the error.
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
    return f"Error log:\n{error_code}: {description}"


async def _dispatch_tool_call(hub: McpHub, tool_name: str, arguments_json: str) -> str:
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse tool arguments: %s", exc)
        return json.dumps({"error": "invalid arguments from LLM"})

    return await hub.call_tool(tool_name, args)


def _parse_llm_json(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", content).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Could not parse JSON from LLM response: {content[:200]}")


async def _check_moderation(client: openai.AsyncOpenAI, text: str) -> None:
    response = await client.moderations.create(input=text)
    result = response.results[0]
    if result.flagged:
        flagged_categories = [
            cat for cat, flagged in result.categories.model_dump().items() if flagged
        ]
        raise HTTPException(
            status_code=400,
            detail=f"Input rejected by content moderation. Flagged categories: {flagged_categories}",
        )


async def run_triage(
    error_code: str,
    description: str,
    client: openai.AsyncOpenAI,
    *,
    mcp_hub: McpHub | None = None,
) -> TriageResponse:
    await _check_moderation(client, f"{error_code}: {description}")

    hub = mcp_hub
    owns_hub = hub is None
    if hub is None:
        hub = McpHub()
        await hub.start()

    try:
        return await _run_triage_loop(error_code, description, client, hub)
    finally:
        if owns_hub:
            await hub.stop()


async def _run_triage_loop(
    error_code: str,
    description: str,
    client: openai.AsyncOpenAI,
    hub: McpHub,
) -> TriageResponse:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(error_code, description)},
    ]

    if hub.k8s_available:
        logger.info("Kubernetes MCP connected — cluster tools available after KB lookup")
    elif settings.k8s_mcp_enabled:
        logger.info(
            "Kubernetes MCP enabled but unavailable (%s) — continuing with KB only",
            hub.k8s_error or "not connected",
        )

    docs_consulted: list[str] = []
    evidence = EvidenceSources()
    last_iteration = settings.agent_max_iterations - 1

    for iteration in range(settings.agent_max_iterations):
        logger.debug("Agent iteration %d/%d", iteration + 1, settings.agent_max_iterations)

        # Final iteration is synthesis only: earlier steps may consume tool rounds
        # (mandatory KB on iter 0, optional K8s evidence after). Without this,
        # a tight budget with K8s enabled can be KB + cluster tools with no answer slot.
        if iteration == last_iteration:
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
            )
        elif iteration == 0:
            tools = hub.kb_tool_schemas()
            tool_choice: str | dict = "required"
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
            )
        else:
            tools = hub.all_tool_schemas()
            tool_choice = "auto"
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
            )

        choice = response.choices[0]
        assistant_msg = choice.message
        messages.append(assistant_msg.model_dump(exclude_unset=True))

        if choice.finish_reason == "tool_calls" and assistant_msg.tool_calls:
            for tool_call in assistant_msg.tool_calls:
                tool_name = tool_call.function.name
                logger.info("Agent calling tool: %s", tool_name)

                result_str = await _dispatch_tool_call(
                    hub, tool_name, tool_call.function.arguments
                )

                classified = hub.classify_tool(tool_name)
                if classified:
                    channel, label = classified
                    if channel == "knowledge_base":
                        evidence.knowledge_base = label
                    elif channel == "kubernetes" and label not in evidence.kubernetes:
                        evidence.kubernetes.append(label)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                })

                if tool_name == KB_TOOL_NAME:
                    try:
                        result_data = json.loads(result_str)
                        entries = result_data.get("matched_entries", [])
                        logger.info("KB tool returned %d matched entries", len(entries))
                        for entry in entries:
                            title = entry.get("title", entry.get("id", "unknown"))
                            if title not in docs_consulted:
                                docs_consulted.append(title)
                    except (json.JSONDecodeError, AttributeError) as e:
                        logger.warning("Failed to parse KB tool result: %s", e)

            continue

        if choice.finish_reason in ("stop", "length"):
            content = assistant_msg.content or ""
            parsed = _parse_llm_json(content)

            return TriageResponse(
                summary=parsed.get("summary", "No summary provided."),
                confidence_score=int(parsed.get("confidence_score", 50)),
                action_items=parsed.get("action_items", []),
                docs_consulted=docs_consulted,
                raw_error=f"{error_code}: {description}",
                evidence_sources=evidence,
            )

        logger.error("Unexpected finish_reason: %s", choice.finish_reason)
        break

    logger.warning("Agent exhausted iterations without a clean answer.")
    return TriageResponse(
        summary="Agent could not determine root cause within the allowed reasoning steps.",
        confidence_score=0,
        action_items=["Escalate to Senior Dev — automated triage was inconclusive."],
        docs_consulted=docs_consulted,
        raw_error=f"{error_code}: {description}",
        evidence_sources=evidence,
    )
