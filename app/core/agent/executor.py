from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from agents import FunctionTool, Tool
from agents.tool_context import ToolContext
from pydantic import BaseModel, ValidationError

from app.core.agent.types import AgentContext

ToolImpl = Callable[[AgentContext, BaseModel], Any]  # sync; run via asyncio.to_thread


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    impl: ToolImpl
    timeout_s: float = 10.0
    max_retries: int = 1
    # Nested DSL unions + defaults don't survive OpenAI strict-mode schema rewriting cleanly, and
    # strict mode isn't honored across all OpenAI-compatible providers — so default to non-strict.
    strict_schema: bool = False
    summarize: Callable[[Any], str] = field(default=lambda _: "")


def _ms(t0: float) -> float:
    return (perf_counter() - t0) * 1000


def _payload(result: Any) -> Any:
    return result.model_dump() if isinstance(result, BaseModel) else result


class ToolExecutor:
    """Registry of :class:`ToolSpec` that exposes them to the Agent as wrapped ``FunctionTool``s."""

    def __init__(self, specs: list[ToolSpec]):
        self._specs = specs

    def as_agent_tools(self) -> list[Tool]:
        return [self._build(spec) for spec in self._specs]

    def _build(self, spec: ToolSpec) -> FunctionTool:
        async def on_invoke(ctx: ToolContext[AgentContext], args_json: str) -> Any:
            return await self._invoke(spec, ctx.context, args_json)

        return FunctionTool(
            name=spec.name,
            description=spec.description,
            params_json_schema=spec.input_model.model_json_schema(),
            on_invoke_tool=on_invoke,
            strict_json_schema=spec.strict_schema,
        )

    async def _invoke(
        self, spec: ToolSpec, agent_ctx: AgentContext, args_json: str
    ) -> Any:
        trace = agent_ctx.trace
        t0 = perf_counter()

        # 1. Validate arguments. A bad call is returned to the model as a typed, correctable error.
        try:
            args = spec.input_model.model_validate_json(args_json or "{}")
        except ValidationError as exc:
            trace.add_step(
                "tool",
                spec.name,
                "error",
                latency_ms=_ms(t0),
                summary="invalid arguments",
                detail={"errors": json.loads(exc.json())},
            )
            return {
                "status": "error",
                "error": "invalid_arguments",
                "detail": json.loads(exc.json()),
            }

        # 2. Run off the event loop with a timeout, retrying a bounded number of times.
        last_exc: Exception | None = None
        for attempt in range(spec.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(spec.impl, agent_ctx, args),
                    timeout=spec.timeout_s,
                )
                payload = _payload(result)
                status = "ok"
                if isinstance(payload, dict):
                    if payload.get("status") == "error":
                        status = "error"
                    elif payload.get("empty"):
                        status = "empty"
                trace.add_step(
                    "tool",
                    spec.name,
                    status,
                    latency_ms=_ms(t0),
                    summary=spec.summarize(payload),
                    detail={"attempt": attempt} if attempt else None,
                )
                return payload
            except (
                TimeoutError,
                asyncio.TimeoutError,
            ) as exc:  # noqa: UP041 - explicit for clarity
                last_exc = exc
            except Exception as exc:  # impl-raised; retry then surface as a typed error
                last_exc = exc

        exc_repr = repr(last_exc)
        trace.add_step(
            "tool",
            spec.name,
            "error",
            latency_ms=_ms(t0),
            summary=f"failed after {spec.max_retries + 1} attempt(s)",
            detail={"error": exc_repr},
        )
        return {"status": "error", "error": "tool_failed", "detail": exc_repr}
