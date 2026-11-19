"""What an agent's last messages say, and the almost-right answer hiding in them.

A weak model can spend its whole call budget and end with no structured answer. `summarise` records
why (the rejected-answer file), and `salvage` recovers an answer from the same messages without a
model call. Nothing here imports `invoke`.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

LAST_MESSAGES = 6
TEXT_LIMIT = 2000
_IMAGE_PLACEHOLDER = "[image]"
_FENCE = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class Salvaged:
    data: dict
    route: str  # "invalid_call" | "answer_call" | "text"


def _text(message: Any, *, images: bool = False) -> str:
    """The text of `message`: a string, or the text parts of a content list. With `images`, each
    image part reads `[image]` (never its data)."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content or []:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            kind = part.get("type")
            if kind == "text":
                parts.append(str(part.get("text", "")))
            elif images and kind in ("image", "image_url"):
                parts.append(_IMAGE_PLACEHOLDER)
    return "".join(parts)


def summarise(messages: list, limit: int = TEXT_LIMIT) -> list[dict]:
    """The last `LAST_MESSAGES` of `messages`, oldest first, as plain dicts for the rejected-answer
    record. Text, call arguments and raw argument strings are cut to `limit` characters."""
    rows: list[dict] = []
    for message in messages[-LAST_MESSAGES:]:
        row: dict[str, Any] = {"role": getattr(message, "type", "unknown"), "text": _text(message, images=True)[:limit]}
        if row["role"] == "ai":
            calls = getattr(message, "tool_calls", None) or []
            if calls:
                row["tool_calls"] = [
                    {"name": call.get("name", ""), "args": json.dumps(call.get("args", {}), default=str)[:limit]} for call in calls
                ]
            invalid = getattr(message, "invalid_tool_calls", None) or []
            if invalid:
                row["invalid_tool_calls"] = [
                    {"name": call.get("name") or "", "args": (call.get("args") or "")[:limit], "error": call.get("error")}
                    for call in invalid
                ]
            metadata = getattr(message, "response_metadata", None) or {}
            reason = metadata.get("finish_reason") or metadata.get("stop_reason")
            if reason:
                row["finish_reason"] = reason
        rows.append(row)
    return rows


def _balanced_end(text: str, start: int) -> int | None:
    """The index just past the `}` that closes the `{` at `start`, ignoring braces inside strings."""
    depth = 0
    in_string = escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def extract_json(text: str) -> str | None:
    """The JSON a model put in `text`: the first fenced block, else the span from the first `{` to
    its matching `}` (to the end of the text when it never closes, for the repair to finish)."""
    fenced = _FENCE.search(text)
    if fenced:
        body = fenced.group(1).strip()
        if body:
            return body
    start = text.find("{")
    if start < 0:
        return None
    end = _balanced_end(text, start)
    return text[start:end] if end is not None else text[start:]


def _object_from(raw: str) -> dict | None:
    """The object `raw` holds, as written or repaired; None when it holds none."""
    from phil.agents.tool_args import repair_arguments  # imports langchain: kept out of import time

    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if isinstance(value, dict):
        return value
    return repair_arguments(raw) or None


def salvage(messages: list, tool_name: str, validate: Callable[[dict], list[str]]) -> tuple[Salvaged | None, list[str]]:
    """The first answer in the last `LAST_MESSAGES` AI messages (newest first) that `validate`
    accepts (no problems), and the problems of the last candidate tried, each prefixed `salvage: `."""
    from phil.agents.tool_args import repair_arguments  # imports langchain: kept out of import time

    problems: list[str] = []

    def accepted(data: dict | None, route: str) -> Salvaged | None:
        nonlocal problems
        if data is None:
            return None
        found = validate(data)
        if not found:
            return Salvaged(data=data, route=route)
        problems = [f"salvage: {problem}" for problem in found]
        return None

    ai_messages = [m for m in messages if getattr(m, "type", None) == "ai"]
    for message in reversed(ai_messages[-LAST_MESSAGES:]):
        for call in getattr(message, "invalid_tool_calls", None) or []:
            if call.get("name") == tool_name and call.get("args"):
                if result := accepted(repair_arguments(call["args"]) or None, "invalid_call"):
                    return result, []
        for call in getattr(message, "tool_calls", None) or []:
            if call.get("name") == tool_name and isinstance(call.get("args"), dict):
                if result := accepted(call["args"], "answer_call"):
                    return result, []
        raw = extract_json(_text(message))
        if raw and (result := accepted(_object_from(raw), "text")):
            return result, []
    return None, problems
