from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings.

    Field names map to UPPER_SNAKE_CASE env vars (e.g. ``llm_base_url`` <- ``LLM_BASE_URL``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Allow ``embed_model`` and similar field names without protected-namespace warnings.
        protected_namespaces=(),
    )

    # --- LLM: chat / tool-calling (any OpenAI-compatible endpoint) ---
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    # Comma-separated, primary first; any later entries act as fallbacks.
    llm_models: str = "openai/gpt-4o-mini"
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2

    # --- Embeddings ---
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "text-embedding-3-small"

    # --- Retrieval (RAG over the guidelines corpus) ---
    guidelines_dir: str = "guidelines"
    cache_dir: str = ".cache"
    retrieval_top_k: int = 4

    # --- Agent run budgets ---
    max_turns: int = 8
    run_budget_s: float = 45.0

    # --- Circuit breaker thresholds ---
    breaker_fail_threshold: int = 3
    breaker_cooldown_s: float = 15.0

    # --- Data / databases ---
    source_db_path: str = "data/data.sqlite"  # provided dataset, read-only
    app_db_path: str = "data/app.sqlite"  # generated state; ":memory:" in tests
    as_of_date: str = "2026-06-24"  # fixed "today" for recency calculations

    @property
    def model_chain(self) -> list[str]:
        """Ordered list of chat models (primary first), parsed from ``llm_models``."""
        return [m.strip() for m in self.llm_models.split(",") if m.strip()]

    @property
    def primary_model(self) -> str:
        chain = self.model_chain
        return chain[0] if chain else ""

    @property
    def llm_configured(self) -> bool:
        """True when a chat LLM looks reachable (key set, or a local endpoint)."""
        local = any(
            host in self.llm_base_url for host in ("localhost", "127.0.0.1", "ollama")
        )
        return bool(self.llm_api_key) or local

    @property
    def embeddings_configured(self) -> bool:
        """True when an embedding provider is configured."""
        return bool(self.embed_base_url) and (
            bool(self.embed_api_key) or "localhost" in self.embed_base_url
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
