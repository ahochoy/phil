"""A small TOML writer: enough for Phil's settings (scalars, lists of scalars and nested tables)."""

import json
import math
import re
from collections.abc import Mapping

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def toml_key(key: str) -> str:
    return key if _BARE_KEY.match(key) else json.dumps(key, ensure_ascii=False)


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} as TOML")


def dump_toml(data: Mapping, comments: Mapping[str, str] | None = None, *, _prefix: tuple[str, ...] = ()) -> str:
    """`data` as TOML. `comments` maps a dotted leaf path (`run.max_cost_usd`) to a trailing comment.

    `None` values are left out: TOML has no null.
    """
    comments = comments or {}
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, Mapping) or value is None:
            continue
        line = f"{toml_key(key)} = {toml_value(value)}"
        comment = comments.get(".".join((*_prefix, key)))
        lines.append(f"{line}  # {comment}" if comment else line)
    out = "\n".join(lines) + ("\n" if lines else "")
    for key, value in data.items():
        if not isinstance(value, Mapping):
            continue
        path = (*_prefix, key)
        header = ".".join(toml_key(part) for part in path)
        out += ("\n" if out else "") + f"[{header}]\n" + dump_toml(value, comments, _prefix=path)
    return out
