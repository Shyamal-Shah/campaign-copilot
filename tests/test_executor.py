from __future__ import annotations

import time

import pytest
from pydantic import BaseModel

from app.core.agent.types import AgentContext
from app.core.agent.executor import ToolExecutor, ToolSpec
from app.core.observability.trace import RunTrace
from app.shared.config import get_settings


class Args(BaseModel):
    x: int


def _ctx() -> AgentContext:
    return AgentContext(db=None, settings=get_settings(), trace=RunTrace())


def _spec(impl, **over) -> ToolSpec:
    kw = dict(name="t", description="d", input_model=Args, impl=impl)
    kw.update(over)
    return ToolSpec(**kw)


async def _invoke(spec, ctx, args_json):
    return await ToolExecutor([spec])._invoke(spec, ctx, args_json)


@pytest.mark.asyncio
async def test_invalid_arguments_return_typed_error_and_trace_step():
    ctx = _ctx()
    out = await _invoke(_spec(lambda c, a: {"ok": True}), ctx, '{"x": "not-an-int"}')
    assert out["status"] == "error" and out["error"] == "invalid_arguments"
    assert ctx.trace.steps[-1].status == "error"


@pytest.mark.asyncio
async def test_success_records_ok_step_and_returns_payload():
    ctx = _ctx()
    out = await _invoke(_spec(lambda c, a: {"doubled": a.x * 2}), ctx, '{"x": 21}')
    assert out == {"doubled": 42}
    assert ctx.trace.steps[-1].status == "ok"
    assert ctx.trace.steps[-1].latency_ms is not None


@pytest.mark.asyncio
async def test_empty_result_is_marked_empty():
    ctx = _ctx()
    await _invoke(_spec(lambda c, a: {"empty": True, "count": 0}), ctx, '{"x": 1}')
    assert ctx.trace.steps[-1].status == "empty"


@pytest.mark.asyncio
async def test_impl_returned_error_is_marked_error():
    ctx = _ctx()
    await _invoke(
        _spec(lambda c, a: {"status": "error", "error": "nope"}), ctx, '{"x": 1}'
    )
    assert ctx.trace.steps[-1].status == "error"


@pytest.mark.asyncio
async def test_timeout_becomes_typed_error():
    def slow(c, a):
        time.sleep(0.2)
        return {"ok": True}

    ctx = _ctx()
    out = await _invoke(_spec(slow, timeout_s=0.01, max_retries=0), ctx, '{"x": 1}')
    assert out["status"] == "error" and out["error"] == "tool_failed"
    assert ctx.trace.steps[-1].status == "error"


@pytest.mark.asyncio
async def test_retry_recovers_from_a_transient_failure():
    attempts = {"n": 0}

    def flaky(c, a):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        return {"ok": True}

    ctx = _ctx()
    out = await _invoke(_spec(flaky, max_retries=1), ctx, '{"x": 1}')
    assert out == {"ok": True}
    assert attempts["n"] == 2
    assert ctx.trace.steps[-1].detail == {"attempt": 1}
