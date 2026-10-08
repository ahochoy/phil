"""The chat's welcome banner (spec 2026-10-08): a boxed card — mascot left, facts right — with
"pick up where you left off" and tips below. Every box line has the same display width."""

from dataclasses import dataclass

from rich.text import Text

from phil.ui.mascot import MASCOT

NARROW_MASCOT = 60
NARROW_BOX = 40
GAP = 4
TIPS = "Type a goal to start · /btw ask while it works · /feed filter the feed · /help everything else"
FIRST_TIPS = "Describe what you want built. Phil plans it, asks before it runs, and works on its own branch. · /help"


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


def _fit(text: Text, cells: int) -> Text:
    out = text.copy()
    if out.cell_len > cells:
        out.truncate(max(cells, 0), overflow="ellipsis")
    return out


def _pad(text: Text, cells: int) -> Text:
    out = _fit(text, cells)
    out.append(" " * max(cells - out.cell_len, 0))
    return out


def _facts(f: BannerFacts) -> list[Text]:
    ident = Text()
    ident.append("Phil", "phil.brand")
    ident.append(f" v{f.version} · deterministic orchestration, token-efficient", "phil.muted")
    where = Text()
    where.append(f.repo, "bold")
    if f.base:
        where.append(f" · base {f.base} {f.sha}", "phil.muted")
    else:
        where.append(" @ ", "phil.muted")
        where.append(f.branch)
        where.append(f" {f.sha}", "phil.muted")
        if f.dirty:
            where.append(" · ", "phil.muted")
            where.append(f"⚠ {f.dirty} uncommitted", "phil.warn")
    if f.parked:
        where.append(f" · {f.parked} parked", "phil.muted")
    models = Text()
    for i, (label, name) in enumerate(f.models):
        if i:
            models.append(" · ", "phil.muted")
        models.append(f"{label} ", "phil.muted")
        models.append(name)
    budget = Text(f"budget ${f.budget_usd:.2f} a run" if f.budget_usd > 0 else "no run budget", "phil.muted")
    return [ident, Text(), where, models, budget]


def _below(f: BannerFacts) -> list[Text]:
    lines: list[Text] = []
    if f.run:
        run_id, state, summary = f.run
        if state == "escalated":
            shown = (summary or "")[:60]
            lines.append(Text(f"⏸ {run_id} is waiting for you ({shown}) · /answer", "phil.warn"))
        elif state == "failed":
            lines.append(Text(f"{run_id} failed · /resume"))
        elif state == "stopped":
            lines.append(Text(f"{run_id} was stopped · /resume"))
        elif state in ("running", "pending"):
            lines.append(Text(f"{run_id} is running · phil attach {run_id}"))
    if f.other_chats:
        n = len(f.other_chats)
        lines.append(Text(f"{n} other open chat{'s' if n != 1 else ''} · phil --resume {f.other_chats[0]}", "phil.muted"))
    lines.append(Text(FIRST_TIPS if f.first_time else TIPS, "phil.muted"))
    return lines


def _mascot_rows(mascot) -> tuple[list[Text], int]:
    rows = [Text.assemble(*[(text, style) for style, text in row]) for row in mascot]
    width = max((r.cell_len for r in rows), default=0)
    return [_pad(r, width) for r in rows], width


def render_banner(facts: BannerFacts, width: int, mascot=MASCOT) -> list[Text]:
    facts_rows, below = _facts(facts), _below(facts)
    if width < NARROW_BOX:
        return [_fit(t, width - 1) for t in [*facts_rows, *below] if t.plain]
    art, art_w = _mascot_rows(mascot) if width >= NARROW_MASCOT else ([], 0)
    lead = art_w + GAP if art else 0
    inner_cap = width - 1 - 4
    inner = min(max([lead + t.cell_len for t in facts_rows] + [art_w]), inner_cap)
    height = max(len(art), len(facts_rows))
    art_top, fact_top = (height - len(art)) // 2, (height - len(facts_rows)) // 2
    border = "─" * (inner + 2)
    out = [Text(f"╭{border}╮", "phil.muted")]
    for i in range(height):
        row = Text()
        if art:
            a = i - art_top
            row.append_text(art[a] if 0 <= a < len(art) else Text(" " * art_w))
            row.append(" " * GAP)
        f = i - fact_top
        fact = facts_rows[f] if 0 <= f < len(facts_rows) else Text()
        row.append_text(_fit(fact, inner - lead))
        line = Text("│ ", "phil.muted")
        line.append_text(_pad(row, inner))
        line.append(" │", "phil.muted")
        out.append(line)
    out.append(Text(f"╰{border}╯", "phil.muted"))
    return out + [_fit(t, width - 1) for t in below]


def banner_plain(facts: BannerFacts) -> list[str]:
    return [t.plain for t in [*_facts(facts), *_below(facts)] if t.plain]
