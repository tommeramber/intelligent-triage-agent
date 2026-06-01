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


class EvidenceSources(BaseModel):
    """
    Which tool backends contributed to the answer (for demos and smoke tests).

    knowledge_base is set when the KB tool ran (always on successful triage).
    kubernetes lists K8s MCP tool names when cluster evidence was gathered.
    """

    knowledge_base: str | None = Field(
        default=None,
        description="KB channel: mcp | in_process_fallback",
    )
    kubernetes: List[str] = Field(
        default_factory=list,
        description="Kubernetes MCP tools invoked (empty if none).",
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

    # Surface the docs the tool retrieved so callers can audit the reasoning chain.
    docs_consulted: List[str] = Field(
        default_factory=list,
        description="Titles of the troubleshooting doc entries the agent consulted.",
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
