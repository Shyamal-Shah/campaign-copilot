from __future__ import annotations

from agents import Agent, Model, ModelSettings

from app.core.agent.executor import ToolExecutor
from app.core.agent.types import CopilotOutcome
from app.core.agent.prompts import SYSTEM_PROMPT
from app.core.agent.tools import build_executor


def build_agent(model: Model | str, executor: ToolExecutor | None = None) -> Agent:
    executor = executor or build_executor()
    return Agent(
        name="campaign_copilot",
        instructions=SYSTEM_PROMPT,
        model=model,
        tools=executor.as_agent_tools(),
        output_type=CopilotOutcome,
        model_settings=ModelSettings(include_usage=True),
    )
