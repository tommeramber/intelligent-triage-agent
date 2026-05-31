"""
Centralised configuration loaded from environment variables.

All tunables live here so that:
  1. Nothing is hard-coded in business logic.
  2. A single ConfigMap / Secret in Kubernetes controls runtime behaviour.
  3. Adding a new setting is one line — no grep-and-replace hunt.

Usage:
    from app.config import settings
    print(settings.openai_model)
"""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Reads values from environment variables (case-insensitive).
    Falls back to the defaults defined here when a variable is absent.
    """

    # ── LLM provider ─────────────────────────────────────────────────────────
    openai_api_key: str = Field(
        default="",
        description="OpenAI secret key. Set via OPENAI_API_KEY env var or K8s Secret.",
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="Model to use. gpt-4o-mini is cheap & fast; swap to gpt-4o for accuracy.",
    )
    llm_max_tokens: int = Field(default=1024, description="Max tokens the LLM may generate.")
    llm_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="LLM sampling temperature. 0.0=deterministic, 2.0=very creative. Keep low (0.0-0.3) for consistent triage output.",
    )

    # ── Agent behaviour ───────────────────────────────────────────────────────
    # How many tool-call rounds the agent may execute before giving up.
    # Prevents infinite loops; 3 is plenty for this single-tool agent.
    agent_max_iterations: int = Field(default=3)

    # ── Knowledge-base (mock tool) ────────────────────────────────────────────
    # Path to the JSON file that acts as the "Company Troubleshooting Docs".
    # Override in K8s via ConfigMap-mounted volume if you want live updates.
    docs_file_path: str = Field(default="data/troubleshooting_docs.json")

    # ── Server ────────────────────────────────────────────────────────────────
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8080)
    app_version: str = Field(default="1.0.0")
    log_level: str = Field(default="info")

    model_config = SettingsConfigDict(
        env_file=".env",            # load from .env when running locally
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns a cached singleton Settings object.
    lru_cache ensures the .env file is read exactly once per process.
    """
    return Settings()


# Module-level alias so callers can do `from app.config import settings`
settings = get_settings()
