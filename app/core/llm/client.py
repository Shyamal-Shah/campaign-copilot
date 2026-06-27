from __future__ import annotations

from agents import (
    Model,
    OpenAIChatCompletionsModel,
    set_default_openai_api,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from app.shared.config import Settings

_configured = False


def configure_sdk() -> None:
    """One-time global SDK setup: use Chat Completions (not Responses) and don't ship traces off-box."""
    global _configured
    if _configured:
        return
    set_default_openai_api(
        "chat_completions"
    )  # non-OpenAI providers don't support the Responses API
    set_tracing_disabled(True)  # we keep our own RunTrace; no OpenAI trace export
    _configured = True


def make_chat_model(settings: Settings) -> Model | None:
    """Build the primary chat model, or ``None`` when no LLM is configured (offline boot)."""
    if not settings.llm_configured:
        return None
    configure_sdk()
    client = AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "not-needed",  # local servers ignore the key
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
    return OpenAIChatCompletionsModel(
        model=settings.primary_model, openai_client=client
    )
