"""The activity feed's lines (spec 2026-10-07 §3.5): compact tool lines under each task and
highlighted milestone bands. Shared by the chat and `phil attach`."""

from datetime import datetime

from rich.cells import cell_len
from rich.text import Text

from phil.ui.toolbar import elapsed

BURST_LINES = 20
INDENT = "    "
_READS = ("read_file",)
# Look-only tools: consecutive calls of these by one task and role fold into one line.
_LOOKS = ("read_file", "ls", "glob", "grep")
_TIMED = ("run_shell", "gate")


def _seconds(ts: object) -> float | None:
    """An ISO timestamp as epoch seconds, or None if it doesn't parse."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _int_seq(item: dict) -> int | None:
    seq = item.get("seq")
    return seq if isinstance(seq, int) and not isinstance(seq, bool) else None


def interleave(events: list[dict], records: list[dict]) -> list[tuple[str, object]]:
    """`events` and activity `records` read in one poll, in display order: `("records", [...])` and
    `("event", e)` items. An event carrying an int `seq` (a milestone: the activity log's last seq
    when it was written) comes after the records up to that seq; an event without one keeps its
    place in file order. Records left after the last event come last. Empty record groups are
    left out."""
    out: list[tuple[str, object]] = []
    unplaced = list(records)
    for event in events:
        upto = _int_seq(event)
        if upto is not None:
            before, after = [], []
            for record in unplaced:
                seq = _int_seq(record)
                (before if seq is not None and seq <= upto else after).append(record)
            unplaced = after
            if before:
                out.append(("records", before))
        out.append(("event", event))
    if unplaced:
        out.append(("records", unplaced))
    return out


def _cut(text: str, cells: int) -> str:
    if cells <= 1:
        return "…" if cells == 1 else ""
    if cell_len(text) <= cells:
        return text
    out = ""
    for char in text:
        if cell_len(out + char) > cells - 1:
            break
        out += char
    return out + "…"


class FeedRenderer:
    def __init__(self) -> None:
        self._task_started: dict[str, float] = {}

    def _line(self, record: dict, width: int, summary: str | None = None) -> Text:
        summary = summary if summary is not None else str(record.get("summary", ""))
        verb, _, rest = summary.partition(" ")
        if record.get("tool") == "gate":
            verb = "gate"
        elif cell_len(verb) > 5:
            # no clean short verb (e.g. a spaceless fallback summary): show the whole
            # summary as the body instead of letting an oversized verb blow the width.
            verb, rest = "", summary
        result_part = f"  {record['result']}" if record.get("result") else ""
        duration_part = ""
        if record.get("tool") in _TIMED and record.get("duration_ms"):
            duration_part = f" · {record['duration_ms'] / 1000:.1f}s"
        ref = f"  #{record['seq']}" if record.get("detail") else ""
        head = f"{INDENT}{verb:<5} "
        tail_len = cell_len(result_part) + cell_len(duration_part)
        budget = max(width - 1 - cell_len(head) - cell_len(ref), 0)
        if cell_len(rest) + tail_len > budget and duration_part:
            duration_part = ""  # narrow: drop the duration first
            tail_len = cell_len(result_part)
        rest = _cut(rest, max(budget - tail_len, 0))
        if not rest and cell_len(result_part) > budget:
            result_part = _cut(result_part, budget)  # narrower still: the result itself must give way
        line = Text(f"{head}{rest}", style="phil.muted")
        if result_part:
            fail = record.get("ok") is False
            line.append(result_part, style="phil.gate.fail" if fail else "phil.muted")
        if duration_part:
            line.append(duration_part, style="phil.muted")
        if ref:
            line.append(ref, style="phil.ref")
        return line

    def tool_lines(self, records: list[dict], width: int) -> list[Text]:
        ends = [r for r in records if r.get("phase") == "end"]
        groups: list[list[dict]] = []
        for record in ends:
            last = groups[-1] if groups else None
            if (last and record.get("tool") in _LOOKS and last[-1].get("tool") in _LOOKS
                    and last[-1].get("task") == record.get("task") and last[-1].get("role") == record.get("role")):
                last.append(record)
            else:
                groups.append([record])
        lines: list[Text] = []
        for group in groups[:BURST_LINES]:
            if len(group) > 1:
                parts = " · ".join(str(r.get("summary", "")).partition(" ")[2] for r in group)
                verb = "read" if all(r.get("tool") in _READS for r in group) else "look"
                lines.append(self._line(group[-1], width, summary=f"{verb} {parts}"))
            else:
                lines.append(self._line(group[0], width))
        extra = groups[BURST_LINES:]
        if extra:
            count = sum(len(g) for g in extra)
            noun = "reads" if all(r.get("tool") in _LOOKS for g in extra for r in g) else "steps"
            lines.append(Text(f"{INDENT}… {count} more {noun}", style="phil.muted"))
        return lines

    def milestone(self, event: dict, width: int) -> Text:
        kind, task = event.get("kind"), event.get("task") or ""
        if kind == "task_started":
            started = _seconds(event.get("ts"))
            if started is not None:
                self._task_started[task] = started
            return Text(f"▸ {task} {event.get('title', '')}".rstrip(), style="phil.band.start")
        if kind == "gate":
            words = {"red": "red: the new tests fail as expected", "green": "green: tests pass",
                     "check": "check passed"}.get(event.get("name"), f"{event.get('name')} passed")
            return Text(f"✓ {task} {words}", style="phil.band.pass")
        if kind == "attempt_failed":
            attempt, limit = event.get("attempt", 1), event.get("limit", 1)
            retrying = bool(event.get("retrying"))
            then = f"retrying ({attempt + 1} of {limit})" if retrying else "needs you"
            return Text(f"✗ {task} attempt {attempt} failed: {event.get('problem', '')} · {then}",
                        style="phil.band.fail" if retrying else "phil.band.wait")
        if kind == "task_done":
            files = event.get("files")
            text = f"✓ {task} done" if files is None else f"✓ {task} done · {files} file{'s' if files != 1 else ''}"
            started, ended = self._task_started.pop(task, None), _seconds(event.get("ts"))
            if started is not None and ended is not None:
                text += f" · {elapsed(ended - started)}"
            return Text(text, style="phil.band.pass")
        if kind == "verdict":
            role, issues, blocking = event.get("role"), event.get("issues", 0), event.get("blocking", 0)
            if event.get("outcome") == "passed":
                return Text("✓ reviewer approved" if role == "reviewer" else f"✓ {role}: no issues", style="phil.band.pass")
            if role == "reviewer":
                return Text(f"✗ reviewer asked for changes: {issues} issues ({blocking} blocking)", style="phil.band.fail")
            return Text(f"✗ {role}: {issues} issues ({blocking} blocking)", style="phil.band.fail")
        return Text(str(kind), style="phil.muted")
