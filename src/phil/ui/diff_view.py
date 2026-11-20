"""The changed lines under a burst summary (spec 2026-11-19): what each edit added and removed,
read from the edit's saved detail. Pure: callers pass the calls and a way to read a call's detail."""

import re
from collections.abc import Callable

from rich.text import Text

from phil.text import printable
from phil.ui.bursts import EDITS, _cut, edited_files, one_line

DIFF_LINES = 8  # changed lines shown per file
DIFF_FILES = 3  # files shown per burst
HEADER_INDENT = " " * 8
LINE_INDENT = " " * 10
_ADDED = re.compile(r"\+(\d+)")
_REMOVED = re.compile(r"−(\d+)")


def changed_lines(tool: str, detail: str) -> tuple[list[tuple[str, str]], bool]:
    """The `("+" | "−", text)` lines of an edit's or write's detail, and whether the detail was cut
    short (ended with the activity log's `… (N more characters not shown)` marker)."""
    rows = detail.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    truncated = bool(rows) and rows[-1].startswith("… (") and rows[-1].endswith(" more characters not shown)")
    if truncated:
        rows.pop()
    if tool == "write_file":
        return [("+", row) for row in rows], truncated
    if tool != "edit_file":
        return [], truncated
    lines: list[tuple[str, str]] = []
    for row in rows[2:]:  # the `--- before` and `+++ after` headers
        if row.startswith("@@"):
            continue
        if row.startswith("+"):
            lines.append(("+", row[1:]))
        elif row.startswith("-"):
            lines.append(("−", row[1:]))
    return lines, truncated


def _total(pattern: re.Pattern[str], calls: list[dict]) -> int:
    total = 0
    for call in calls:
        match = pattern.search(str(call.get("result") or ""))
        if match:
            total += int(match.group(1))
    return total


def _styled(text: str, style: str) -> Text:
    """A line styled by a span over its whole text (not the Text's base style)."""
    line = Text(text)
    line.stylize(style)
    return line


def file_blocks(calls: list[dict], details: Callable[[int], str | None], width: int, *,
                more_hint: bool, number: int | None) -> list[Text]:
    """Every diff line for one burst: for up to DIFF_FILES files a header (`a.py  +3 −1`) and that
    file's changed lines, then `+N more files`. Failed calls and non-edits are left out."""
    edits = [call for call in calls if call.get("tool") in EDITS and call.get("ok") is not False]
    files = edited_files(edits)
    if not files:
        return []
    ref = f" · /more #{number}" if more_hint and number is not None else ""
    room = max(width - 1, 0)
    out: list[Text] = []
    for path in files[:DIFF_FILES]:
        mine = [call for call in edits if one_line(call.get("summary", "")).partition(" ")[2] == path]
        header = f"{HEADER_INDENT}{path}  +{_total(_ADDED, mine)} −{_total(_REMOVED, mine)}"
        out.append(_styled(_cut(header, room), "phil.muted"))
        lines: list[tuple[str, str]] = []
        truncated = False
        for call in mine:
            seq = call.get("seq")
            detail = details(seq) if isinstance(seq, int) and not isinstance(seq, bool) else None
            if detail:
                got, cut = changed_lines(str(call.get("tool")), detail)
                lines += got
                truncated = truncated or cut
        for marker, text in lines[:DIFF_LINES]:
            style = "phil.diff.add" if marker == "+" else "phil.diff.del"
            out.append(_styled(_cut(f"{LINE_INDENT}{marker} {printable(text.expandtabs(4).rstrip())}", room), style))
        if len(lines) > DIFF_LINES:
            out.append(_styled(_cut(f"{LINE_INDENT}… {len(lines) - DIFF_LINES} more lines", room), "phil.muted"))
        if truncated:
            hint = f" in /more #{number}" if more_hint and number is not None else ""
            out.append(_styled(_cut(f"{LINE_INDENT}… more{hint}", room), "phil.muted"))
    if len(files) > DIFF_FILES:
        out.append(_styled(_cut(f"{HEADER_INDENT}+{len(files) - DIFF_FILES} more files{ref}", room), "phil.muted"))
    return out
