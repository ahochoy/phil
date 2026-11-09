"""A model that answers in text instead of calling its answer tool gets a second chance.

Phil's models never force the answer tool (phil.agents.tool_choice), so a model can end its turn
in plain text. After each model call: text that is the answer, one JSON object, bare or in one
``` fence, valid for the contract, is taken as the structured response; any other text gets a
reminder to call the tool, at most MAX_REMINDERS times per attempt; then the turn ends with no
answer, and invoke_agent's contract retry takes over (spec 2026-10-22 §4.2).
"""

import json
import re
from typing import Any

from pydantic import ValidationError

from phil.contracts import Contract

MAX_REMINDERS = 2
REMINDER_ID = "phil-reminder"  # message ids, so reminders can be counted; never sent to providers
_FENCE = re.compile(r"```[a-zA-Z]*[ \t]*\n?(.*?)```", re.DOTALL)


def reminder_text(tool_name: str, *, tools: bool = False) -> str:
    """The reminder. An agent with tools may have been narrating mid-task, so it isn't told to stop."""
    if tools:
        return f"Continue with your tools; when you're done, return your answer by calling the {tool_name} tool."
    return f"Return your answer by calling the {tool_name} tool."


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append(str(part.get("text", "")))
    return "".join(parts)


def json_answer(text: str, contract: type[Contract]) -> Contract | None:
    """The contract `text` holds as one JSON object, bare or inside exactly one ``` fence, or None."""
    fences = _FENCE.findall(text)
    if len(fences) > 1:
        return None
    candidate = (fences[0] if fences else text).strip()
    if not candidate.startswith("{"):
        return None
    try:
        data = json.loads(candidate)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return contract.model_validate(data)
    except ValidationError:
        return None


def reminder_count(messages: list) -> int:
    return sum(
        1
        for m in messages
        if getattr(m, "type", None) == "human" and str(getattr(m, "id", None) or "").startswith(REMINDER_ID)
    )


def answer_reminder_middleware(contract: type[Contract], *, tools: bool = False) -> Any:
    """`tools`: the agent has tools to work with (light and deep harnesses), not only its answer."""
    from langchain.agents.middleware import AgentMiddleware, hook_config
    from langchain_core.messages import HumanMessage

    text = reminder_text(contract.__name__, tools=tools)

    def after(state: Any) -> dict[str, Any] | None:
        if state.get("structured_response") is not None:
            return None
        messages = state["messages"]
        last = messages[-1] if messages else None
        if getattr(last, "type", None) != "ai" or getattr(last, "tool_calls", None):
            return None
        answer = json_answer(_text(last.content), contract)
        if answer is not None:
            return {"structured_response": answer, "jump_to": "end"}
        sent = reminder_count(messages)
        if sent >= MAX_REMINDERS:
            return {"jump_to": "end"}
        reminder = HumanMessage(text, id=f"{REMINDER_ID}-{sent + 1}")
        return {"messages": [reminder], "jump_to": "model"}

    class AnswerReminder(AgentMiddleware):
        @hook_config(can_jump_to=["model", "end"])
        def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            return after(state)

        @hook_config(can_jump_to=["model", "end"])
        async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            return after(state)

    return AnswerReminder()
