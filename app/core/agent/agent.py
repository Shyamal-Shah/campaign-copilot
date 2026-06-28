from __future__ import annotations

import sqlite3

from agents import Agent, Model, ModelSettings
from agents.agent import ToolsToFinalOutputResult
from agents.run_context import RunContextWrapper
from agents.tool import FunctionToolResult

from app.core.agent.executor import ToolExecutor
from app.core.agent.prompts import build_system_prompt
from app.core.agent.tools import build_executor
from app.core.agent.types import PlannerState
from app.shared.config import Settings


def _planner_finished(
    wrapper: RunContextWrapper[PlannerState], results: list[FunctionToolResult]
) -> ToolsToFinalOutputResult:
    """Stop the tool loop once PlannerState reached a terminal state — a campaign was persisted
    (``create_campaign``) or the agent declined (``finish``). A *failed* create (too-broad, no
    ``campaign_id``) is NOT terminal, so the model gets to fix the draft and retry. The outcome is
    read from PlannerState by the router, never from the model's free text.

    Load-bearing: without it the SDK runs the LLM again after the terminal tool, costing an extra
    round-trip and — on models that don't cleanly stop — running to max_turns into the degraded path.
    """
    ctx = wrapper.context
    if ctx.campaign_id or ctx.finish_status:
        return ToolsToFinalOutputResult(is_final_output=True, final_output=ctx.finish_message)
    return ToolsToFinalOutputResult(is_final_output=False, final_output=None)


def build_agent(
    model: Model | str,
    conn: sqlite3.Connection,
    settings: Settings,
    executor: ToolExecutor | None = None,
) -> Agent:
    executor = executor or build_executor()
    return Agent(
        name="campaign_copilot",
        instructions=build_system_prompt(conn, settings),
        model=model,
        tools=executor.as_agent_tools(),
        model_settings=ModelSettings(include_usage=True),
        tool_use_behavior=_planner_finished,
    )
