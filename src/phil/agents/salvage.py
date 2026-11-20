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
MAX_CALLS_PER_MESSAGE = 10
_FENCE_OPEN = re.compile(r"^[ \t]*```(?:json)?[ \t]*$", re.IGNORECASE)


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
            invalid = getattr(message, "invalid_tool_calls", None) or []
            if calls:
                row["tool_calls"] = [
                    {"name": call.get("name", ""), "args": json.dumps(call.get("args", {}), default=str)[:limit]}
                    for call in calls[:MAX_CALLS_PER_MESSAGE]
                ]
            if invalid:
                row["invalid_tool_calls"] = [
                    {"name": call.get("name") or "", "args": (call.get("args") or "")[:limit], "error": call.get("error")}
                    for call in invalid[:MAX_CALLS_PER_MESSAGE]
                ]
            omitted = max(len(calls) - MAX_CALLS_PER_MESSAGE, 0) + max(len(invalid) - MAX_CALLS_PER_MESSAGE, 0)
            if omitted:
                row["tool_calls_omitted"] = omitted
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


def _fenced_blocks(text: str) -> list[str]:
    """The bodies of the ```` ``` ```` and ```` ```json ```` fences in `text`. A fence with any other
    info string (```` ```bash ````) is skipped whole, and a fence closes only on a line that is exactly
    ```` ``` ````, so a closing fence is never mistaken for an opening one."""
    blocks: list[str] = []
    body: list[str] | None = None
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        if body is not None:
            if stripped == "```":
                blocks.append("\n".join(body).strip())
                body = None
            else:
                body.append(line)
        elif skipping:
            skipping = stripped != "```"
        elif _FENCE_OPEN.match(line):
            body = []
        elif stripped.startswith("```"):
            skipping = True
    return [block for block in blocks if block]


def json_candidates(text: str) -> list[str]:
    """Every piece of `text` that might be a JSON answer: each fenced block, then each top-level
    `{...}` span (the last one runs to the end of the text when it never closes, for the repair to
    finish), in order."""
    candidates = _fenced_blocks(text)
    position = text.find("{")
    while position >= 0:
        end = _balanced_end(text, position)
        if end is None:
            candidates.append(text[position:])
            break
        candidates.append(text[position:end])
        position = text.find("{", end)
    return candidates


def extract_json(text: str) -> str | None:
    """The first JSON candidate in `text` (see `json_candidates`), or None."""
    candidates = json_candidates(text)
    return candidates[0] if candidates else None


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


def _candidates(message: Any, tool_name: str) -> list[tuple[dict | None, str]]:
    """The answers `message` offers, in the order to try them: its invalid answer calls, its answer
    calls, then the JSON in its text. A `None` object is an answer call that could not be read."""
    from phil.agents.tool_args import repair_arguments  # imports langchain: kept out of import time

    found: list[tuple[dict | None, str]] = []
    for call in getattr(message, "invalid_tool_calls", None) or []:
        if call.get("name") == tool_name and call.get("args"):
            found.append((repair_arguments(call["args"]) or None, "invalid_call"))
    for call in getattr(message, "tool_calls", None) or []:
        if call.get("name") == tool_name and isinstance(call.get("args"), dict):
            found.append((call["args"], "answer_call"))
    for raw in json_candidates(_text(message)):
        data = _object_from(raw)
        if data is not None:
            found.append((data, "text"))
    return found


def salvage(
    messages: list,
    tool_name: str,
    validate: Callable[[dict], list[str]],
    fields: frozenset[str] = frozenset(),
) -> tuple[Salvaged | None, list[str]]:
    """The answer in the newest AI message that offers one, if `validate` accepts it.

    Only the newest attempt counts: the walk goes back through the last `LAST_MESSAGES` AI messages
    to the first with any candidate (an answer call, an invalid answer call, or JSON in its text),
    tries each of that message's candidates, and stops there. When none validates it returns
    `(None, problems)` for the last candidate tried, each problem prefixed `salvage: `; an older
    message's draft is never used. An empty answer is never accepted, and a text candidate must set
    at least one of `fields` (the contract's field names) when they are given."""
    ai_messages = [m for m in messages if getattr(m, "type", None) == "ai"]
    for message in reversed(ai_messages[-LAST_MESSAGES:]):
        candidates = _candidates(message, tool_name)
        if not candidates:
            continue
        problems: list[str] = []
        for data, route in candidates:
            if data is None:
                found = ["the answer call could not be read"]
            elif not data:
                found = ["the answer is empty"]
            elif route == "text" and fields and not fields & data.keys():
                found = [f"the answer sets none of the contract's fields ({', '.join(sorted(fields))})"]
            else:
                found = validate(data)
                if not found:
                    return Salvaged(data=data, route=route), []
            problems = [f"salvage: {problem}" for problem in found]
        return None, problems
    return None, []
