from __future__ import annotations

import json
from time import perf_counter

from agents import RunHooks

from app.core.observability.trace import RunTrace

_MAX_CONTENT_CHARS = 2000


def _truncate(text: str, limit: int = _MAX_CONTENT_CHARS) -> str:
    """Shorten text beyond *limit* with a char-count suffix."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\u2026 [{len(text)} chars total]"


def _summarize_input(system_prompt: str, input_items) -> dict:
    """Condense the LLM call's inputs into a trace-friendly dict."""
    detail: dict = {}
    if system_prompt:
        detail["system_prompt"] = _truncate(str(system_prompt), 500)
    if isinstance(input_items, str):
        detail["input"] = _truncate(input_items, 500)
    elif isinstance(input_items, list):
        detail["input_count"] = len(input_items)
        # Keep the last 3 items for context (most recent turns).
        tail = input_items[-3:] if len(input_items) > 3 else input_items
        serialized = []
        for item in tail:
            try:
                s = json.dumps(
                    item.model_dump() if hasattr(item, "model_dump") else item,
                    default=str,
                )
            except Exception:
                s = str(item)
            serialized.append(_truncate(s, 800))
        detail["recent_items"] = serialized
    return detail


def _extract_output(response) -> dict:
    """Extract readable text and tool-call info from an LLM response."""
    texts: list[str] = []
    tool_calls: list[dict] = []
    for item in getattr(response, "output", []):
        # Text from message-type outputs.
        content = getattr(item, "content", None)
        if content:
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for part in content:
                    text = getattr(part, "text", None)
                    if text:
                        texts.append(text)
        # Tool/function call info.
        name = getattr(item, "name", None)
        if name:
            tool_calls.append(
                {
                    "call_id": getattr(item, "call_id", None),
                    "name": name,
                    "arguments": _truncate(getattr(item, "arguments", "") or "", 500),
                }
            )
    result: dict = {}
    if texts:
        result["text"] = _truncate("\n".join(texts))
    if tool_calls:
        result["tool_calls"] = tool_calls
    return result


class TracingHooks(RunHooks):
    def __init__(self, trace: RunTrace):
        self._trace = trace
        self._llm_t0: float | None = (
            None  # runs are sequential within one loop, so a scalar is fine
        )

    async def on_agent_start(self, context, agent) -> None:
        self._trace.add_step("note", "agent_start", "ok", summary=agent.name)

    async def on_agent_end(self, context, agent, output) -> None:
        self._trace.add_step("note", "agent_end", "ok", summary=type(output).__name__)

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        self._llm_t0 = perf_counter()
        self._trace.add_step(
            "model",
            "llm_start",
            "ok",
            summary=f"{len(input_items)} input item(s)",
            detail=_summarize_input(system_prompt, input_items),
        )

    async def on_llm_end(self, context, agent, response) -> None:
        latency = (
            (perf_counter() - self._llm_t0) * 1000 if self._llm_t0 is not None else None
        )
        self._llm_t0 = None
        usage = response.usage
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        total = getattr(usage, "total_tokens", 0) or 0
        output_content = _extract_output(response)
        self._trace.add_step(
            "model",
            "llm_end",
            "ok",
            latency_ms=latency,
            summary=f"{total} tok ({inp}+{out})",
            detail={
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": total,
                "response_id": response.response_id,
                **output_content,
            },
        )
