"""
Pydantic data models for request/response validation and serialization.

These models serve as the contract between the API consumer and the agent:
- TriageRequest  : what comes IN  (a single-key JSON object: {error_code: description})
- TriageResponse : what goes OUT  (the structured analysis)
"""

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator
from typing import Any, Dict, List

VALID_ERROR_CODES = {
    "400", "401", "403", "404", "405", "408",
    "409", "422", "429",
    "500", "502", "503", "504"
}


class TriageRequest(RootModel[Dict[str, str]]):
    """
    Request body: a single-key JSON object where the key is the error code
    and the value is the error description.

    Example: {"500": "DB connection refused to postgres:5432"}
    """
    model_config = ConfigDict(
        json_schema_extra={
            "description": f"Single-key JSON object. Valid error codes: {sorted(VALID_ERROR_CODES)}",
            "example": {"500": "DB connection refused to postgres:5432"},
        }
    )

    @model_validator(mode="before")
    @classmethod
    def validate_request(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        if len(value) != 1:
            raise ValueError("Request must contain exactly one key: the error code.")

        code, description = next(iter(value.items()))

        if code not in VALID_ERROR_CODES:
            raise ValueError(
                f"Unknown error code '{code}'. Valid codes: {sorted(VALID_ERROR_CODES)}"
            )

        if not isinstance(description, str) or not description.strip():
            raise ValueError("Error description cannot be empty.")

        stripped = description.strip()

        if len(stripped) < 5:
            raise ValueError("Error description is too short (minimum 5 characters).")

        if len(stripped) > 2000:
            raise ValueError("Error description is too long (maximum 2000 characters).")

        return {code: stripped}

    @property
    def error_code(self) -> str:
        return next(iter(self.root))

    @property
    def description(self) -> str:
        return next(iter(self.root.values()))


class KubernetesEvidence(BaseModel):
    """
    Kubernetes MCP usage for this triage run.

    invoked lists tools the agent called. status summarizes the run:
      not_invoked — no K8s tools called
      obtained — workload evidence from an allowed namespace (pods, logs, events, …)
      no_accessible_evidence — tools ran but only allowlist/meta or inconclusive results
      access_denied — at least one call blocked by namespace opt-in / guard policy

    evidence_obtained is True only when status is obtained.
    """

    invoked: List[str] = Field(
        default_factory=list,
        description="Kubernetes MCP tool names the agent invoked.",
    )
    status: str = Field(
        default="not_invoked",
        description=(
            "not_invoked | obtained | no_accessible_evidence | access_denied"
        ),
    )
    message: str | None = Field(
        default=None,
        description="Short, demo-friendly explanation of kubernetes provenance.",
    )
    evidence_obtained: bool = Field(
        default=False,
        description="True when status is obtained (workload evidence, not allowlist-only).",
    )


class EvidenceSources(BaseModel):
    """
    Which tool backends contributed to the answer (for demos and smoke tests).

    knowledge_base is set when the KB tool ran (always on successful triage).
    kubernetes reports tools invoked vs whether cluster evidence was actually obtained.
    """

    knowledge_base: str | None = Field(
        default=None,
        description="KB channel: mcp | in_process_fallback",
    )
    kubernetes: KubernetesEvidence = Field(
        default_factory=KubernetesEvidence,
        description="Kubernetes MCP invocation and whether cluster evidence was obtained.",
    )


class TriageResponse(BaseModel):
    """Structured analysis the agent returns after reasoning over the error."""

    summary: str = Field(
        ...,
        description="One-sentence human-readable description of the root problem.",
    )
    confidence_score: int = Field(
        ...,
        ge=0,
        le=100,
        description="Agent's confidence in its analysis, 0-100.",
    )
    action_items: List[str] = Field(
        ...,
        description="Ordered list of recommended remediation steps.",
        examples=[["Restart Pod", "Check DB Credentials", "Escalate to Senior Dev"]],
    )

    # Tool-sourced KB titles (not LLM-claimed). See ARCHITECTURE.md.
    docs_consulted: List[str] = Field(
        default_factory=list,
        description=(
            "Runbook titles that matched description keywords (up to 3). "
            "Empty when the KB had only weak code-only matches."
        ),
    )
    docs_useful: List[str] = Field(
        default_factory=list,
        description=(
            "Top keyword-grounded runbook title(s) used as primary grounding evidence."
        ),
    )
    kb_keyword_match: bool = Field(
        default=False,
        description=(
            "True when the error description matched at least one KB keyword; "
            "False means no keyword-grounded runbooks (code-only or no KB hits)."
        ),
    )

    raw_error: str = Field(
        ...,
        description="Echo of the original error as '{error_code}: {description}'",
    )

    evidence_sources: EvidenceSources = Field(
        default_factory=EvidenceSources,
        description="MCP / fallback backends used while building this answer.",
    )


class HealthResponse(BaseModel):
    """Simple liveness check payload."""

    status: str
    version: str


class ReadinessResponse(BaseModel):
    """Deep readiness probe payload — includes OpenAI reachability status."""

    status: str           # "ready" or "not_ready"
    openai_reachable: bool
    model: str
    detail: str | None = None
