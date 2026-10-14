"""One layout for a callout box (spec 2026-10-07 callouts §3.2): a `Decision` or a `Failure`
rendered as lines of `(style_name, text)` segments, then turned into prompt_toolkit fragments
for the docked prompt or a Rich `Group` for the scrollback and `LineIO`'s plain lines.

Never pass the decision's or failure's text through Rich markup: every segment here is literal
text, built directly into `Text` objects or plain fragments (ruling: Rendering)."""

import re

from rich.cells import cell_len
from rich.console import Group
from rich.text import Text

from phil.agents.failures import Failure
from phil.chat.decision import BODY_LINES, DETAILS, SEE_MORE, Decision, Option
from phil.ui.feed_view import _cut

NARROW = 40
KEY_HINT = "↑/↓ choose · Enter confirm · 1–{n} · Esc type a message"
NUMBER_KEYS = 9  # the digits that pick an option

Line = list[tuple[str, str]]


def _take(text: str, width: int) -> tuple[str, str]:
    """The longest prefix of `text` whose cell width is at most `width`, and the remainder."""
    out = ""
    for i, char in enumerate(text):
        if cell_len(out + char) > width:
            return out, text[i:]
        out += char
    return text, ""


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word-wrap measured in cells (not characters), so wide characters never overflow.

    A line's leading whitespace (e.g. the two-space indent on an approval's command line) is
    kept and repeated on every wrapped line; interior runs of whitespace may collapse to a
    single space. `width` is always at least 1 by construction (every caller clamps it), so
    there's no `width <= 0` branch here."""
    indent = text[: len(text) - len(text.lstrip(" "))]
    indent_width = cell_len(indent)
    body = text[len(indent):]
    inner = max(width - indent_width, 1)
    lines: list[str] = []
    current = ""
    for word in body.split(" "):
        if not word:
            continue
        candidate = f"{current} {word}" if current else word
        if cell_len(candidate) <= inner:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while cell_len(word) > inner:
            head, word = _take(word, inner)
            if not head:  # a single character wider than the whole box; take it anyway
                head, word = word[:1], word[1:]
            lines.append(head)
        current = word
    lines.append(current)
    return [indent + line for line in lines]


def _wrap_spaced(text: str, width: int) -> list[str]:
    """Like `_wrap`, but runs of spaces inside a line are kept as they are (an approval's
    `<command>  (<label>)  → saves <rule>`, and a command's own spacing); only the run at a line
    break is dropped. A word wider than the line is split, never cut with `…` (R15)."""
    indent = text[: len(text) - len(text.lstrip(" "))]
    inner = max(width - cell_len(indent), 1)
    tokens = [token for token in re.split(r"( +)", text[len(indent):]) if token]
    lines: list[str] = []
    current = ""
    for token in tokens:
        if token.startswith(" "):
            current += token  # a gap: kept unless the next word breaks the line
            continue
        if cell_len(current + token) <= inner:
            current += token
            continue
        if current.strip():
            lines.append(current.rstrip(" "))
        current = ""
        while cell_len(token) > inner:
            head, token = _take(token, inner)
            if not head:
                head, token = token[:1], token[1:]
            lines.append(head)
        current = token
    lines.append(current.rstrip(" "))
    return [indent + line for line in lines]


def _inner_width(width: int) -> int:
    bordered = width >= NARROW
    return max((width - 1 - 4) if bordered else (width - 1 - 2), 1)


def _cut_row(row: Line, inner: int) -> Line:
    """`row` cut to `inner` cells, as a general backstop: a row built elsewhere (e.g. an
    option's `"› N "` prefix at a width narrower than the prefix itself) might still be too
    wide, so this cuts the overrun off the end, with `…` on whichever segment it lands in."""
    if sum(cell_len(t) for _, t in row) <= inner:
        return row
    out: Line = []
    remaining = inner
    for style, t in row:
        if remaining <= 0:
            break
        if cell_len(t) <= remaining:
            out.append((style, t))
            remaining -= cell_len(t)
        else:
            out.append((style, _cut(t, remaining)))
            remaining = 0
    return out


def _pad_row(row: Line, inner: int, border_style: str | None) -> Line:
    row = _cut_row(row, inner)
    length = sum(cell_len(t) for _, t in row)
    pad = max(inner - length, 0)
    content = [*row, ("", " " * pad)] if pad else list(row)
    if border_style:
        return [(border_style, "│ "), *content, (border_style, " │")]
    return [("", "  "), *content]


def _frame(rows: list[Line], width: int, kind: str) -> list[Line]:
    bordered = width >= NARROW
    inner = _inner_width(width)
    border_style = f"phil.callout.border.{kind}"
    lines: list[Line] = []
    if bordered:
        lines.append([(border_style, "╭" + "─" * (inner + 2) + "╮")])
    for row in rows:
        lines.append(_pad_row(row, inner, border_style if bordered else None))
    if bordered:
        lines.append([(border_style, "╰" + "─" * (inner + 2) + "╯")])
    return lines


def _option_row(option: Option, index: int, highlighted: int, inner: int, live: bool) -> Line:
    selected = live and index == highlighted
    prefix = f"{'›' if selected else ' '} {index + 1} "
    label = _cut(option.label, max(inner - cell_len(prefix), 0))
    style = "phil.callout.selected" if selected else "phil.callout.option"
    return [(style, f"{prefix}{label}")]


def _body_rows(decision: Decision, inner: int) -> tuple[list[str], bool]:
    """`decision`'s body wrapped to `inner` cells and capped at BODY_LINES rows (the last one
    then `… see /more 1`), so the docked box fits a 24-row terminal; and whether rows were cut."""
    if decision.kind == "approval":
        return _approval_rows(decision.body, inner)
    rows = [line for entry in decision.body for line in _wrap(entry, inner)]
    if len(rows) <= BODY_LINES:
        return rows, False
    return [*rows[: BODY_LINES - 1], SEE_MORE], True


RULE_ARROW = "  → "


def _approval_entry(entry: str, inner: int) -> list[str]:
    """One approval body line: whole when it fits; otherwise the command and label, then its
    `→ saves <rule>` on rows of its own, indented, so a rule is never split from its arrow."""
    if cell_len(entry) <= inner:
        return [entry]
    head, arrow, tail = entry.partition(RULE_ARROW)
    if not arrow:
        return _wrap_spaced(entry, inner)
    return [*_wrap_spaced(head, inner), *_wrap_spaced(f"    → {tail}", inner)]


REST = "… see /more 1 for the rest"


def hidden_pointer(hidden: int) -> str:
    """The last body line of an approval that leaves `hidden` entries out (N2)."""
    things = "command and rule" if hidden == 1 else "commands and rules"
    return f"… {hidden} more {things} — see /more 1 before choosing Always"


def _approval_rows(body: tuple[str, ...], inner: int) -> tuple[list[str], bool]:
    """An approval's body (R15, N2): its first line, then one entry per command. Every line wraps,
    keeping its spacing, and is never cut with `…`. Taller than BODY_LINES rows: the entries that
    fit are drawn whole, then a pointer saying how many are left out. When not even the first
    command fits, as many of its rows as fit are drawn, then `… see /more 1 for the rest`, so
    the box always shows a command. /more 1 holds them all."""
    entries = [_approval_entry(entry, inner) for line in body for entry in str(line).splitlines()]
    if sum(len(rows) for rows in entries) <= BODY_LINES:
        return [row for rows in entries for row in rows], False
    head, commands = (entries[0], entries[1:]) if entries else ([], [])
    room = BODY_LINES - len(head)
    reserve = len(_wrap(hidden_pointer(len(commands)), inner))  # the widest the pointer gets
    kept: list[str] = []
    count = 0
    for rows in commands:
        if len(kept) + len(rows) > room - reserve:
            break
        kept.extend(rows)
        count += 1
    if count == 0 and commands:
        rest = _wrap(REST, inner)
        return [*head, *commands[0][: max(room - len(rest), 1)], *rest], True
    return [*head, *kept, *_wrap(hidden_pointer(len(commands) - count), inner)], True


def overflows(decision: Decision, width: int) -> bool:
    """Whether `decision`'s body is cut in a box `width` cells wide."""
    return _body_rows(decision, _inner_width(width))[1]


def callout_lines(decision: Decision, highlighted: int, width: int, *, live: bool) -> list[Line]:
    """`decision`'s box: the title, the body (wrapped, at most BODY_LINES rows), the options
    (numbered; `live` adds the `›` highlight marker), each option's detail, and, when `live`,
    the key hint."""
    kind = decision.kind
    inner = _inner_width(width)
    title_style = f"phil.callout.title.{kind}"
    body_style = "phil.callout.body"

    rows: list[Line] = [[(title_style, line)] for line in _wrap(decision.title, inner)]
    rows.extend([(body_style, line)] for line in _body_rows(decision, inner)[0])

    if decision.options:
        rows.append([])
        for index, option in enumerate(decision.options):
            rows.append(_option_row(option, index, highlighted, inner, live))
            if option.detail:
                rows.extend(
                    [(body_style, "    " + line)] for line in _wrap(option.detail, max(inner - 4, 1))
                )

    if live:
        rows.append([])
        hint = KEY_HINT.format(n=min(len(decision.options), NUMBER_KEYS))
        rows.extend([("phil.callout.hint", line)] for line in _wrap(hint, inner))

    return _frame(rows, width, kind)


def failure_lines(failure: Failure, width: int) -> list[Line]:
    """`failure`'s box: `✗ <headline>`, then `<retries> <action>` (wrapped), then the details ref."""
    kind = "failure"
    inner = _inner_width(width)
    title_style = f"phil.callout.title.{kind}"
    body_style = "phil.callout.body"

    rows: list[Line] = [[(title_style, line)] for line in _wrap(f"✗ {failure.headline}", inner)]
    rows.extend([(body_style, line)] for line in _wrap(f"{failure.retries} {failure.action}", inner))
    rows.extend([(body_style, line)] for line in _wrap(DETAILS, inner))

    return _frame(rows, width, kind)


def to_rich(lines: list[Line]) -> Group:
    """`lines` as a Rich `Group`, one `Text` per line, styled by name (not Rich markup)."""
    texts = []
    for line in lines:
        rendered = Text()
        for style, segment in line:
            rendered.append(segment, style=style or None)
        texts.append(rendered)
    return Group(*texts)


def to_fragments(lines: list[Line]) -> list[tuple[str, str]]:
    """`lines` as prompt_toolkit fragments: each style name becomes `class:<name>`, and every
    line ends with a plain `"\\n"` fragment."""
    fragments: list[tuple[str, str]] = []
    for line in lines:
        for style, segment in line:
            if segment:
                fragments.append((f"class:{style}" if style else "", segment))
        fragments.append(("", "\n"))
    return fragments
