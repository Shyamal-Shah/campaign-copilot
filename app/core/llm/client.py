from __future__ import annotations

from collections.abc import Callable

from agents import (
    Model,
    OpenAIChatCompletionsModel,
    set_default_openai_api,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from app.shared.config import Settings

_configured = False


class FallbackModel(Model):
    """Tries an ordered list of models per call, rotating to the next on any error.

    This is the single env-configured fallback layer: if the primary model errors on a call,
    the next one serves it. When *every* model raises, the error propagates so the run's degraded
    planner (``app.core.agent.fallback``) can take over. 
    """

    def __init__(
        self,
        models: list[Model],
        on_failover: Callable[[int, Exception], None] | None = None,
    ):
        if not models:
            raise ValueError("FallbackModel needs at least one model")
        self._models = models
        self._on_failover = on_failover  # optional hook: (index_that_failed, exception)

    async def get_response(self, *args, **kwargs):
        last_exc: Exception | None = None
        for i, model in enumerate(self._models):
            try:
                return await model.get_response(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if self._on_failover is not None:
                    self._on_failover(i, exc)
        assert last_exc is not None
        raise last_exc

    def stream_response(self, *args, **kwargs):
        return self._models[0].stream_response(*args, **kwargs)


def configure_sdk() -> None:
    """One-time global SDK setup: use Chat Completions (not Responses) and don't ship traces off-box."""
    global _configured
    if _configured:
        return
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)  # we keep our own RunTrace; no OpenAI trace export
    _configured = True


def make_chat_model(settings: Settings) -> Model | None:
    """Build the chat model, or ``None`` when no LLM is configured (offline boot).

    With a single entry in ``LLM_MODELS`` this is one model; with several it's a
    :class:`FallbackModel` over the chain (primary first), all sharing one client.
    """
    if not settings.llm_configured:
        return None
    configure_sdk()
    client = AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "not-needed",  # local servers ignore the key
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
    models: list[Model] = [
        OpenAIChatCompletionsModel(model=name, openai_client=client)
        for name in settings.model_chain
    ]
    if not models:
        return None
    return models[0] if len(models) == 1 else FallbackModel(models)
