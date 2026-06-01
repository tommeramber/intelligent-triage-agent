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
    # Agent loop length: iteration 0 requires KB; middle rounds may call K8s MCP;
    # the last iteration is synthesis-only (no tools). Default 4 = KB + up to two
    # evidence rounds + final answer — enough for demo K8s inspection without
    # unbounded tool loops.
    agent_max_iterations: int = Field(default=4, ge=2)

    # ── Knowledge-base ──────────────────────────────────────────────────────────
    # Path to the JSON file that acts as the "Company Troubleshooting Docs".
    # Override in K8s via ConfigMap-mounted volume if you want live updates.
    docs_file_path: str = Field(default="data/troubleshooting_docs.json")

    # ── MCP (stdio subprocesses in the same container / host) ───────────────────
    mcp_kb_enabled: bool = Field(
        default=True,
        description="Spawn the local KB MCP server over stdio (same pod).",
    )
    mcp_kb_command: str = Field(
        default="python3",
        description="Executable to run the KB MCP server module.",
    )
    mcp_kb_args: str = Field(
        default="-m mcp_servers.kb_server",
        description="Space-separated args for the KB MCP server process.",
    )
    mcp_pythonpath: str = Field(
        default="",
        description="PYTHONPATH for MCP child processes (empty = project /app root).",
    )
    mcp_workdir: str = Field(
        default="",
        description="Working directory for MCP child processes (empty = project /app root).",
    )

    k8s_mcp_enabled: bool = Field(
        default=False,
        description="Spawn the in-cluster Python K8s MCP server (pod ServiceAccount).",
    )
    k8s_mcp_command: str = Field(
        default="python3",
        description="Executable for the hardened K8s MCP server module.",
    )
    k8s_mcp_args: str = Field(
        default="-m mcp_servers.k8s_server",
        description="Args for the Python K8s MCP server (in-cluster, read-only).",
    )
    k8s_mcp_kubeconfig: str = Field(
        default="",
        description="Optional kubeconfig for local dev only (in-cluster config is preferred).",
    )
    k8s_access_label_key: str = Field(
        default="triage.agent-accessible",
        description="Namespace label key that opts workloads in for triage inspection.",
    )
    k8s_access_label_value: str = Field(
        default="true",
        description="Required value for the namespace opt-in label.",
    )
    k8s_access_allowlist: str = Field(
        default="",
        description="Comma-separated namespace allowlist (synced by make k8s-sync-access).",
    )

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


    def mcp_kb_args_list(self) -> list[str]:
        return self.mcp_kb_args.split() if self.mcp_kb_args else []

    def k8s_mcp_args_list(self) -> list[str]:
        return self.k8s_mcp_args.split() if self.k8s_mcp_args else []

    def k8s_mcp_env_map(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.k8s_mcp_kubeconfig:
            env["KUBECONFIG"] = self.k8s_mcp_kubeconfig
        return env

    def k8s_access_allowlist_set(self) -> set[str]:
        if not self.k8s_access_allowlist.strip():
            return set()
        return {
            part.strip()
            for part in self.k8s_access_allowlist.split(",")
            if part.strip()
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns a cached singleton Settings object.
    lru_cache ensures the .env file is read exactly once per process.
    """
    return Settings()


# Module-level alias so callers can do `from app.config import settings`
settings = get_settings()
