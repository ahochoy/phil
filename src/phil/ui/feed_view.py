"""The activity feed's lines (spec 2026-10-07 §3.5): compact tool lines under each task and
highlighted milestone bands. Shared by the chat and `phil attach`; burst state lives here
(spec 2026-10-16 §5, §7)."""

from datetime import datetime

from rich.cells import cell_len
from rich.text import Text

from phil.ui.bursts import (
    ENGINE, INDENT, LOOKS, TIMED, Burst, FeedItem, burst_text, fold, group_summary, is_failure, key_of,
    rolling_lines,
)
from phil.ui.toolbar import elapsed

BURST_LINES = 20


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
    def __init__(self, *, more_hint: bool = True) -> None:
        self._task_started: dict[str, float] = {}
        self._more_hint = more_hint  # `(/more #n)` on summaries; attach has no /more (Ruling R18)
        self._burst: Burst | None = None  # the burst still running
        self._bursts: dict[int, list[dict]] = {}  # closed bursts by number, for /more #n

    def _line(self, record: dict, width: int, summary: str | None = None, style: str = "phil.muted") -> Text:
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
        if record.get("tool") in TIMED and record.get("duration_ms"):
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
        line = Text(f"{head}{rest}", style=style)
        if result_part:
            fail = record.get("ok") is False
            line.append(result_part, style="phil.gate.fail" if fail else "phil.muted")
        if duration_part:
            line.append(duration_part, style="phil.muted")
        if ref:
            line.append(ref, style="phil.ref")
        return line

    def tool_lines(self, records: list[dict], width: int, cap: int | None = BURST_LINES) -> list[Text]:
        groups = fold([r for r in records if r.get("phase") == "end"])
        shown, extra = (groups, []) if cap is None else (groups[:cap], groups[cap:])
        lines: list[Text] = []
        for group in shown:
            summary = group_summary(group) if len(group) > 1 else None
            lines.append(self._line(group[-1], width, summary=summary))
        if extra:
            count = sum(len(g) for g in extra)
            noun = "reads" if all(r.get("tool") in LOOKS for g in extra for r in g) else "steps"
            lines.append(Text(f"{INDENT}… {count} more {noun}", style="phil.muted"))
        return lines

    def activity(self, records: list[dict], width: int) -> list[FeedItem]:
        """Feed lines for a batch of activity records (spec 2026-10-16 §5). A record of another
        (task, role) ends the open burst first (its summary line). Within a burst only failures
        print, at once; the engine's gate runs keep their own line each (Ruling R12)."""
        out: list[FeedItem] = []
        for record in records:
            key = key_of(record)
            if self._burst is not None and self._burst.key != key:
                out += self.end_burst(width)
            if record.get("phase") != "end":
                continue
            if key[1] == ENGINE:
                out.append(FeedItem(self._line(record, width), (record,)))
                continue
            if self._burst is None:
                self._burst = Burst(*key)
            self._burst.calls.append(record)
            if is_failure(record):
                out.append(FeedItem(self._line(record, width, style="phil.error"), (record,)))
        return out

    def end_burst(self, width: int) -> list[FeedItem]:
        """The open burst ended: its one summary line (none if nothing is open). It's kept for /more."""
        burst, self._burst = self._burst, None
        if burst is None or not burst.calls:
            return []
        number = burst.number
        if number is not None:
            self._bursts[number] = list(burst.calls)
        ref = f"  (/more #{number})" if self._more_hint and number is not None else ""
        body = _cut(f"{INDENT}{burst_text(burst)}", max(width - 1 - cell_len(ref), 0))
        return [FeedItem(Text(body + ref, style="phil.muted"), tuple(burst.calls))]

    def rolling(self) -> tuple[str, ...]:
        return rolling_lines(self._burst)

    def burst(self, number: int) -> list[dict] | None:
        calls = self._bursts.get(number)
        return list(calls) if calls is not None else None

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
        if kind == "rule_saved":
            # The line is built by the engine (store.events.saved_rules_line); rule text is plain, so Text.
            return Text(str(event.get("line", "")), style="phil.muted" if event.get("ok", True) else "phil.warn")
        return Text(str(kind), style="phil.muted")
