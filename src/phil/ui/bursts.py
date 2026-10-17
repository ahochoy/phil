"""Tool-call bursts (spec 2026-10-16 §5): the tool calls of one agent invocation, shown as a
rolling view while it runs and as one summary line once it ends. Pure: the feed renderer holds
the state. Activity records carry no invocation id, so a burst is one (task, role) run of records
(Ruling R11)."""

from dataclasses import dataclass, field

from rich.cells import cell_len
from rich.text import Text

from phil.text import printable

INDENT = "    "
READS = ("read_file",)
LOOKS = ("read_file", "ls", "glob", "grep")  # consecutive look-only calls by one task and role fold into one
TIMED = ("run_shell", "gate")
EDITS = ("edit_file", "write_file")
ENGINE = "engine"  # the engine's own gate runs: never a burst (Ruling R12)
ROLLING_HEAD = 2  # the calls a long burst keeps at the top of its rolling view
NAMED_EDITS = 3
SHELL_CHARS = 40


@dataclass(frozen=True)
class FeedItem:
    """A feed line and the activity records it stands for (what `/feed <agent>` filters on)."""

    line: Text
    records: tuple[dict, ...]


@dataclass
class Burst:
    task: str | None
    role: str
    calls: list[dict] = field(default_factory=list)  # end records, in order

    @property
    def key(self) -> tuple[str | None, str]:
        return (self.task, self.role)

    @property
    def number(self) -> int | None:
        """What `/more #n` calls it: its last call's seq (Ruling R15)."""
        seq = self.calls[-1].get("seq") if self.calls else None
        return seq if isinstance(seq, int) and not isinstance(seq, bool) else None


def one_line(text: object) -> str:
    """`text` as one printable line: whitespace (newlines too) collapsed, hidden characters shown as `?`."""
    return printable(" ".join(str(text).split()))


def key_of(record: dict) -> tuple[str | None, str]:
    return (record.get("task"), str(record.get("role") or ""))


def is_failure(record: dict) -> bool:
    """An error result, a non-zero exit, a timeout or a refusal: the call's end record says ok=False."""
    return record.get("phase") == "end" and record.get("ok") is False


def fold(ends: list[dict]) -> list[list[dict]]:
    """Consecutive look-only calls by one task and role, as one group. A failed look keeps its own
    group, so its result and #ref are never hidden inside a fold."""
    groups: list[list[dict]] = []
    for record in ends:
        last = groups[-1] if groups else None
        if (last and record.get("tool") in LOOKS and last[-1].get("tool") in LOOKS
                and record.get("ok") is not False and last[-1].get("ok") is not False
                and last[-1].get("task") == record.get("task") and last[-1].get("role") == record.get("role")):
            last.append(record)
        else:
            groups.append([record])
    return groups


def group_summary(group: list[dict]) -> str:
    """One call's summary, or a folded group's: `read a.py · b.py` (all reads) or `look …`."""
    if len(group) == 1:
        return str(group[0].get("summary", ""))
    parts = " · ".join(part for r in group if (part := str(r.get("summary", "")).partition(" ")[2]))
    verb = "read" if all(r.get("tool") in READS for r in group) else "look"
    return f"{verb} {parts}"


def call_text(group: list[dict]) -> str:
    """One rolling-view line: verb, target, result and a timed call's duration (plain text)."""
    record = group[-1]
    summary = one_line(group_summary(group))
    verb, _, rest = summary.partition(" ")
    if record.get("tool") == "gate":
        verb = "gate"
    elif cell_len(verb) > 6:  # no short verb (a sub-agent, a fallback summary): the summary alone
        verb, rest = "", summary
    head = f"{INDENT}{verb:<6} " if verb else INDENT
    result = f"  {one_line(record['result'])}" if record.get("result") else ""
    duration = ""
    if record.get("tool") in TIMED and record.get("duration_ms"):
        duration = f" · {record['duration_ms'] / 1000:.1f}s"
    return f"{head}{rest}{result}{duration}"


def rolling_lines(burst: Burst | None) -> tuple[str, ...]:
    """The main agent's calls so far (sub-agent calls have their own row): all of them up to three
    display items, else the first two, `… N more` and the latest (a folded group is one item)."""
    if burst is None:
        return ()
    own = [call for call in burst.calls if call.get("sub_id") is None]
    items = [call_text(group) for group in fold(own)]
    if len(items) <= ROLLING_HEAD + 1:
        return tuple(items)
    hidden = len(items) - ROLLING_HEAD - 1
    return (*items[:ROLLING_HEAD], f"{INDENT}… {hidden} more", items[-1])


def edited_files(calls: list[dict]) -> list[str]:
    """The files the burst's successful edits and writes touched, in first-touched order."""
    files: list[str] = []
    for call in calls:
        if call.get("tool") in EDITS and call.get("ok") is not False:
            path = str(call.get("summary", "")).partition(" ")[2]
            if path and path not in files:
                files.append(path)
    return files


def last_command(calls: list[dict]) -> str | None:
    for call in reversed(calls):
        if call.get("tool") == "run_shell":
            command = one_line(call.get("summary", "")).removeprefix("run ").strip()
            if command:
                return command
    return None


def burst_text(burst: Burst) -> str:
    """`implementer ran 9 tools · edited a, b, c +2`, `… · ran <command>`, or the count alone."""
    count = len(burst.calls)
    text = f"{burst.role} ran {count} tool{'' if count == 1 else 's'}"
    files = edited_files(burst.calls)
    if files:
        more = f" +{len(files) - NAMED_EDITS}" if len(files) > NAMED_EDITS else ""
        return f"{text} · edited {', '.join(files[:NAMED_EDITS])}{more}"
    command = last_command(burst.calls)
    if command:
        shown = command if len(command) <= SHELL_CHARS else command[: SHELL_CHARS - 1] + "…"
        return f"{text} · ran {shown}"
    return text
