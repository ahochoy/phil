"""Phil's chat models never force a tool call.

LangChain's `create_agent` binds an agent's structured-output tool with `tool_choice="any"`
whenever a `ToolStrategy` is used, with no option to turn it off. Current Claude models (Opus 5.5,
Sonnet 5.5, Fable 5.1) reject a forced tool choice with a 400, and Ollama ignores it. So every
model Phil builds offers its tools unforced; an agent that answers in text instead is handled by
`phil.agents.reminder`. Imports nothing heavy: providers.py imports it at module level.
"""

from functools import cache
from typing import Any


def unforced_choice(tool_choice: Any) -> Any:
    """`tool_choice` without forcing: "any", "required", True, a tool's name, or a dict that
    forces one become "auto"; None, False, "auto", "none" and an auto/none dict pass through."""
    if tool_choice is None or tool_choice is False:
        return tool_choice
    if isinstance(tool_choice, str) and tool_choice in ("auto", "none"):
        return tool_choice
    if isinstance(tool_choice, dict) and tool_choice.get("type") in ("auto", "none"):
        return tool_choice
    return "auto"


class NoForcedToolChoice:
    """Mixin for a LangChain chat model: `bind_tools` never forces a tool call."""

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return super().bind_tools(tools, tool_choice=unforced_choice(tool_choice), **kwargs)  # type: ignore[misc]


@cache
def without_forced_tool_choice(cls: type) -> type:
    """`cls` with `NoForcedToolChoice` in front, named `Phil<cls.__name__>`."""
    return type(f"Phil{cls.__name__}", (NoForcedToolChoice, cls), {"__module__": __name__})
