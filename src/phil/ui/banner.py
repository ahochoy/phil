"""The chat's welcome banner (spec 2026-10-08): a boxed card — mascot left, facts right — with
"pick up where you left off" and tips below. Every box line has the same display width."""

from collections.abc import Sequence
from dataclasses import dataclass

from rich.text import Text

from phil.ui.mascot import MASCOT

NARROW_MASCOT = 60
NARROW_BOX = 40
GAP = 4
MIN_WIDTH = 10
SUMMARY_CHARS = 60
MUTED = "phil.muted"
# Tips are (text, style) segments; command names carry no style, the rest is muted. A tips line
# that doesn't fit drops whole segments: /feed first, then /btw (indexes into TIPS_SEGMENTS).
TIPS_SEGMENTS: tuple[tuple[tuple[str, str], ...], ...] = (
    (("Type a goal to start", MUTED),),
    ((" · ", MUTED), ("/btw", ""), (" ask while it works", MUTED)),
    ((" · ", MUTED), ("/feed", ""), (" filter the feed", MUTED)),
    ((" · ", MUTED), ("/help", ""), (" everything else", MUTED)),
)
TIPS_DROP = (2, 1)
FIRST_TIPS_SEGMENTS: tuple[tuple[tuple[str, str], ...], ...] = (
    (("Describe what you want built. Phil plans it, asks before it runs, and works on its own branch.", MUTED),
     (" · ", MUTED), ("/help", "")),
)
TIPS = "".join(text for segment in TIPS_SEGMENTS for text, _ in segment)
FIRST_TIPS = "".join(text for segment in FIRST_TIPS_SEGMENTS for text, _ in segment)


@dataclass(frozen=True)
class BannerFacts:
    version: str
    repo: str
    branch: str
    sha: str
    base: str | None
    dirty: int
    parked: int
    models: tuple[tuple[str, str], ...]
    budget_usd: float
    run: tuple[str, str, str | None] | None
    other_chats: tuple[str, ...]
    first_time: bool


@dataclass(frozen=True)
class _Line:
    """A line as whole segments; `drop` lists the segments to leave out, in order, when it doesn't fit."""

    segments: tuple[Text, ...]
    drop: tuple[int, ...] = ()

    @property
    def text(self) -> Text:
        return Text("").join(self.segments)

    def fit(self, cells: int) -> Text:
        keep = list(range(len(self.segments)))
        for index in self.drop:
            if sum(self.segments[i].cell_len for i in keep) <= cells:
                break
            keep.remove(index)
        return _fit(Text("").join(self.segments[i] for i in keep), cells)


def _one(text: Text) -> _Line:
    return _Line((text,))


def _fit(text: Text, cells: int) -> Text:
    out = text.copy()
    if out.cell_len > cells:
        out.truncate(max(cells, 0), overflow="ellipsis")
    return out


def _pad(text: Text, cells: int) -> Text:
    out = _fit(text, cells)
    out.append(" " * max(cells - out.cell_len, 0))
    return out


def _facts(f: BannerFacts, plain: bool = False) -> list[_Line]:
    ident = Text()
    ident.append("Phil", "phil.brand")
    ident.append(f" v{f.version} · deterministic orchestration, token-efficient", MUTED)
    where = Text()
    where.append(f.repo, "bold")
    if f.base:
        where.append(f" · base {f.base} {f.sha}", MUTED)
    else:
        where.append(" @ ", MUTED)
        where.append(f.branch)
        where.append(f" {f.sha}", MUTED)
        if f.dirty:
            where.append(" · ", MUTED)
            where.append(f"⚠ {f.dirty} uncommitted", "phil.warn")
            if plain:  # ruling R1: piped output says why they matter; the card stays short
                where.append(" (not included in runs)", "phil.warn")
    if f.parked:
        where.append(f" · {f.parked} parked", MUTED)
    models: list[Text] = []
    for i, (label, name) in enumerate(f.models):
        segment = Text()
        if i:
            segment.append(" · ", MUTED)
        segment.append(f"{label} ", MUTED)
        segment.append(name)
        models.append(segment)
    # The models row drops whole " · <label> <name>" segments from the end, keeping the first.
    models_line = _Line(tuple(models), tuple(range(len(models) - 1, 0, -1)))
    budget = Text(f"budget ${f.budget_usd:.2f} a run" if f.budget_usd > 0 else "no run budget", MUTED)
    return [_one(ident), _one(Text()), _one(where), models_line, _one(budget)]


def _summary(summary: str | None) -> str:
    shown = " ".join((summary or "").split())
    return shown if len(shown) <= SUMMARY_CHARS else shown[: SUMMARY_CHARS - 1] + "…"


def _segments(spec: Sequence[Sequence[tuple[str, str]]]) -> tuple[Text, ...]:
    return tuple(Text.assemble(*[(text, style) if style else text for text, style in segment]) for segment in spec)


def _below(f: BannerFacts) -> list[_Line]:
    lines: list[_Line] = []
    if f.run:
        run_id, state, summary = f.run
        # The banner prints before the chat is chosen, so each hint is a command that works from any chat.
        if state == "escalated":
            shown = _summary(summary)
            why = f" ({shown})" if shown else ""
            lines.append(_one(Text(f"⏸ {run_id} is waiting for you{why} · phil attach {run_id}", "phil.warn")))
        elif state == "failed":
            lines.append(_one(Text(f"{run_id} failed · phil resume {run_id}")))
        elif state == "stopped":
            lines.append(_one(Text(f"{run_id} was stopped · phil resume {run_id}")))
        elif state in ("running", "pending"):
            lines.append(_one(Text(f"{run_id} is running · phil attach {run_id}")))
    if f.other_chats:
        n = len(f.other_chats)
        lines.append(_one(Text(f"{n} other open chat{'s' if n != 1 else ''} · phil --resume {f.other_chats[0]}", MUTED)))
    if f.first_time:
        lines.append(_Line(_segments(FIRST_TIPS_SEGMENTS)))
    else:
        lines.append(_Line(_segments(TIPS_SEGMENTS), TIPS_DROP))
    return lines


def _mascot_rows(mascot) -> tuple[list[Text], int]:
    rows = [Text.assemble(*[(text, style) for style, text in row]) for row in mascot]
    width = max((r.cell_len for r in rows), default=0)
    return [_pad(r, width) for r in rows], width


def render_banner(facts: BannerFacts, width: int, mascot=MASCOT) -> list[Text]:
    width = max(width, MIN_WIDTH)
    facts_rows, below = _facts(facts), _below(facts)
    if width < NARROW_BOX:
        return [t.fit(width - 1) for t in [*facts_rows, *below] if t.text.plain]
    art, art_w = _mascot_rows(mascot) if width >= NARROW_MASCOT else ([], 0)
    lead = art_w + GAP if art else 0
    inner_cap = width - 1 - 4
    inner = min(max([lead + t.text.cell_len for t in facts_rows] + [art_w]), inner_cap)
    height = max(len(art), len(facts_rows))
    art_top, fact_top = (height - len(art)) // 2, (height - len(facts_rows)) // 2
    border = "─" * (inner + 2)
    out = [Text(f"╭{border}╮", MUTED)]
    for i in range(height):
        row = Text()
        if art:
            a = i - art_top
            row.append_text(art[a] if 0 <= a < len(art) else Text(" " * art_w))
            row.append(" " * GAP)
        f = i - fact_top
        fact = facts_rows[f] if 0 <= f < len(facts_rows) else _one(Text())
        row.append_text(fact.fit(inner - lead))
        line = Text("│ ", MUTED)
        line.append_text(_pad(row, inner))
        line.append(" │", MUTED)
        out.append(line)
    out.append(Text(f"╰{border}╯", MUTED))
    return out + [t.fit(width - 1) for t in below]


def banner_plain(facts: BannerFacts) -> list[str]:
    return [t.text.plain for t in [*_facts(facts, plain=True), *_below(facts)] if t.text.plain]
