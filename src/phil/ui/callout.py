"""One layout for a callout box (spec 2026-10-07 callouts §3.2): a `Decision` or a `Failure`
rendered as lines of `(style_name, text)` segments, then turned into prompt_toolkit fragments
for the docked prompt or a Rich `Group` for the scrollback and `LineIO`'s plain lines.

Never pass the decision's or failure's text through Rich markup: every segment here is literal
text, built directly into `Text` objects or plain fragments (ruling: Rendering)."""

from rich.cells import cell_len
from rich.console import Group
from rich.text import Text

from phil.agents.failures import Failure
from phil.chat.decision import Decision, Option
from phil.ui.feed_view import _cut

NARROW = 40
KEY_HINT = "↑/↓ to choose · 1-{n} to pick · Enter to confirm"

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
    """Greedy word-wrap measured in cells (not characters), so wide characters never overflow."""
    if width <= 0:
        return [text]
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if cell_len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while cell_len(word) > width:
            head, word = _take(word, width)
            if not head:  # a single character wider than the whole box; take it anyway
                head, word = word[:1], word[1:]
            lines.append(head)
        current = word
    lines.append(current)
    return lines


def _inner_width(width: int) -> int:
    bordered = width >= NARROW
    return max((width - 1 - 4) if bordered else (width - 1 - 2), 1)


def _pad_row(row: Line, inner: int, border_style: str | None) -> Line:
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


def callout_lines(decision: Decision, highlighted: int, width: int, *, live: bool) -> list[Line]:
    """`decision`'s box: the title, the body (wrapped), the options (numbered; `live` adds the
    `›` highlight marker), each option's detail, and, when `live`, the key hint."""
    kind = decision.kind
    inner = _inner_width(width)
    title_style = f"phil.callout.title.{kind}"
    body_style = "phil.callout.body"

    rows: list[Line] = [[(title_style, line)] for line in _wrap(decision.title, inner)]
    for entry in decision.body:
        rows.extend([(body_style, line)] for line in _wrap(entry, inner))

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
        rows.extend([("phil.callout.hint", line)] for line in _wrap(KEY_HINT.format(n=len(decision.options)), inner))

    return _frame(rows, width, kind)


def failure_lines(failure: Failure, width: int) -> list[Line]:
    """`failure`'s box: `✗ <headline>`, then `<retries> <action>` (wrapped), then the details ref."""
    kind = "failure"
    inner = _inner_width(width)
    title_style = f"phil.callout.title.{kind}"
    body_style = "phil.callout.body"

    rows: list[Line] = [[(title_style, line)] for line in _wrap(f"✗ {failure.headline}", inner)]
    rows.extend([(body_style, line)] for line in _wrap(f"{failure.retries} {failure.action}", inner))
    rows.append([(body_style, "Details: /more 1")])

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
