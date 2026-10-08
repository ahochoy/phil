from dataclasses import dataclass

from rich.cells import cell_len, set_cell_size

from phil.chat.state import ToolbarView
from phil.store.telemetry import format_cost

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SEP = " │ "
BUDGET_WARN = 0.8
MAX_DOTS = 12
Fragments = list[tuple[str, str]]
STEP_LABELS = {
    "intake": "Understanding the goal",
    "snapshot": "Reading the repo",
    "architect": "Architect drafting",
    "revise": "Architect revising",
    "critic": "Critic reviewing",
    "designing": "Proposing approaches",
}  # /btw has no step: in-flight /btw questions show as `/btw ×N`
NODE_LABELS = {
    "setup": "Setting up", "pick_task": "Picking the next task", "implement": "Implementing",
    "verify": "Verifying", "commit": "Committing", "tester": "Testing", "tester_task": "Testing",
    "review": "Reviewing", "finish": "Finishing", "escalate": "Waiting for you",
}

# Keep priority, higher survives longer (spec §2 drop order).
P_EXTRA, P_REPO, P_MODEL, P_TOKENS, P_ELAPSED, P_COST, P_PROGRESS, P_PAUSE = range(8)


@dataclass
class _Segment:
    priority: int
    fragments: Fragments
    glue: str = SEP  # what joins it to the segment before


def elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def short_model(model: str) -> str:
    rest = model.split(":", 1)[1] if ":" in model else model
    return rest.rsplit("/", 1)[-1]


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M tok"
    if n >= 1000:
        return f"{n // 1000}k tok"
    return f"{n} tok"


def task_dots(done: int, total: int, now: float, paused: bool) -> Fragments:
    total, done = max(total, 0), max(min(done, total), 0)
    current = done < total
    if paused:
        cur = ("class:phil.warn", "◉")
    else:
        cur = ("class:phil.gate.pass", "◉") if int(now * 2) % 2 == 0 else ("class:phil.muted", "○")
    if total > MAX_DOTS:
        frags: Fragments = [("class:phil.gate.pass", "●" * min(done, 3)), ("class:phil.muted", "…")]
        if current:
            frags.append(cur)
        frags.append(("class:phil.muted", f" {done + (1 if current else 0)}/{total}"))
        return frags
    frags = [("class:phil.gate.pass", "●")] * done
    if current:
        frags.append(cur)
        frags += [("class:phil.muted", "○")] * (total - done - 1)
    return frags


def budget_style(cost: float, limit: float) -> str:
    if limit <= 0:
        return "class:phil.cost"
    ratio = cost / limit
    if ratio >= 1:
        return "class:phil.error"
    if ratio >= BUDGET_WARN:
        return "class:phil.warn"
    return "class:phil.cost"


def toolbar_text(fragments: Fragments) -> str:
    return "".join(text for _, text in fragments)


def _segments(view: ToolbarView, now: float) -> list[_Segment]:
    segs: list[_Segment] = []
    if view.repo:
        segs.append(_Segment(P_REPO, [("", view.repo), ("class:phil.muted", f" @ {view.branch or '?'}")]))
    run = view.run
    if view.paused and run:
        segs.append(_Segment(P_PAUSE, [("class:phil.warn", f"⏸ {run.run_id} needs you")]))
    if run:
        progress: Fragments = [("class:phil.id", run.run_id), ("", " "),
                               *task_dots(run.tasks_done, run.tasks_total, now, view.paused)]
        if not view.paused:
            progress.append(("", f" {run.node or 'starting'}"))
        segs.append(_Segment(P_PROGRESS, progress))
        if not view.paused:
            segs.append(_Segment(P_ELAPSED, [("", elapsed(now - run.started))], glue=" · "))
    elif view.step:
        frame = SPINNER[int((now - view.step_started) * 8) % len(SPINNER)]
        label = STEP_LABELS.get(view.step, view.step)
        step = f"{frame} {label} · {elapsed(now - view.step_started)}"
        if view.cancelling:
            step += " (cancelling…)"
        segs.append(_Segment(P_PROGRESS, [("", step)]))
    else:
        segs.append(_Segment(P_PROGRESS, [("class:phil.muted", "Phil · type a goal, or /help")]))
    if view.model and not view.paused:
        tier, name = view.model
        segs.append(_Segment(P_MODEL, [("class:phil.muted", f"{tier}·"), ("", name)]))
    if run and view.tokens is not None:
        segs.append(_Segment(P_TOKENS, [("class:phil.muted", format_tokens(view.tokens))]))
    if run and view.run_cost is not None:
        cost, source = view.run_cost
        shown = format_cost(cost, source)
        if view.budget_usd > 0:
            shown += f"/${view.budget_usd:.2f}"
        segs.append(_Segment(P_COST, [(budget_style(cost, view.budget_usd), shown)]))
    elif not run and view.cost is not None:
        segs.append(_Segment(P_COST, [("class:phil.cost", f"chat {format_cost(*view.cost)}")]))
    if view.btw_pending:
        segs.append(_Segment(P_EXTRA, [("class:phil.muted", f"/btw ×{view.btw_pending}")]))
    if view.parked:
        segs.append(_Segment(P_EXTRA, [("class:phil.muted", f"{view.parked} parked")]))
    return segs


def _join(segs: list[_Segment]) -> Fragments:
    out: Fragments = []
    for i, seg in enumerate(segs):
        if i:
            out.append(("class:phil.muted", seg.glue))
        out += seg.fragments
    return out


def render_toolbar(view: ToolbarView, now: float, width: int | None = None) -> Fragments:
    """The status line as prompt_toolkit fragments; with `width`, segments drop (spec §2 order)
    until it fits in fewer cells than that, and a line that still doesn't fit is cut with …."""
    segs = _segments(view, now)
    if width is not None:
        limit = width - 1
        while len(segs) > 1 and cell_len(toolbar_text(_join(segs))) > limit:
            droppable = [s for s in segs if s.priority != P_PAUSE]
            if not droppable:
                break
            victim = min(droppable, key=lambda s: s.priority)
            index = segs.index(victim)
            segs.remove(victim)
            if victim.priority == P_PROGRESS:  # an elapsed segment means nothing without its progress
                segs[:] = [s for s in segs if s.priority != P_ELAPSED]
            if index == 0 and segs:
                segs[0].glue = SEP
    frags = _join(segs)
    if width is not None and cell_len(toolbar_text(frags)) > width - 1:
        # The pause notice alone keeps its warning style when it's cut.
        style = "class:phil.warn" if len(segs) == 1 and segs[0].priority == P_PAUSE else ""
        frags = [(style, _fit(toolbar_text(frags), width))]
    return frags


MAX_LIVE_LINES = 3


def _spin(style: str, started: float, now: float) -> tuple[str, str]:
    return (style, SPINNER[int((now - started) * 8) % len(SPINNER)])


def render_live_rows(
    view: ToolbarView, now: float, width: int | None = None, max_lines: int | None = None
) -> Fragments:
    """The live row(s) above the input: one line per active agent (the main agent, each sub-agent
    indented with `└`), a goal step and in-flight `/btw` questions as their own lines; at most
    `MAX_LIVE_LINES` lines plus a `+N more` line. Fragments, with `"\\n"` between lines and none
    after the last. An empty list means no live row.

    `max_lines=1` collapses everything to the first row alone, with ` +N more` appended inside
    it (still fitted to `width`) when other agents are active — the decision callout's budget."""
    rows: list[Fragments] = []
    if view.run is not None:
        if view.live is not None:
            live = view.live
            parts = [p for p in (live.task, live.role, live.summary, elapsed(now - live.started)) if p]
            rows.append([_spin("class:phil.agent", live.started, now), ("", " " + " · ".join(parts))])
        else:
            node = view.run.node or "starting"
            rows.append([("", "  " + NODE_LABELS.get(node, node))])
        for sub in view.subs:
            what = sub.summary or "working"
            rows.append([_spin("class:phil.sub", sub.started, now),
                         ("", f"  └ sub-agent · {what} · {sub.description} · {elapsed(now - sub.started)}")])
    if view.step:
        label = STEP_LABELS.get(view.step, view.step)
        rows.append([_spin("class:phil.warn" if view.run else "class:phil.agent", view.step_started, now),
                     ("", f" {label} · {elapsed(now - view.step_started)}")])
    for job in view.side:
        rows.append([_spin("class:phil.warn", job.started, now),
                     ("", f" /btw · {job.label} · {elapsed(now - job.started)}")])
    if not rows:
        return []
    if max_lines == 1:
        extra = len(rows) - 1
        first = [*rows[0], ("class:phil.muted", f" +{extra} more")] if extra > 0 else rows[0]
        rows = [first]
    elif len(rows) > MAX_LIVE_LINES + 1:
        rows = [*rows[:MAX_LIVE_LINES], [("class:phil.muted", f"  +{len(rows) - MAX_LIVE_LINES} more")]]
    if view.feed_filter:
        rows[0] = [*rows[0], ("class:phil.muted", f"   [feed: {view.feed_filter}]")]
    out: Fragments = []
    for i, row in enumerate(rows):
        if i:
            out.append(("", "\n"))
        out += _fit_row(row, width)
    return out


_FEED_TAG = "   [feed: "


def _fit_row(row: Fragments, width: int | None) -> Fragments:
    """`row`, cut to fit `width` if needed: the first (spinner) fragment's style survives, and the
    body text is cut with `_fit` before a `[feed: x]` tag, so the tag survives a cut — unless the
    tag alone doesn't fit in `width - 1`, in which case the whole row (tag included) is cut
    instead, so nothing this returns ever exceeds `width - 1` cells."""
    text = toolbar_text(row)
    if width is None or cell_len(text) <= width - 1:
        return row
    style = row[0][0] if row else ""
    index = text.rfind(_FEED_TAG)
    body, tag = (text, "") if index == -1 else (text[:index], text[index:])
    if tag and cell_len(tag) > width - 1:
        return [(style, _fit(text, width))]
    return [(style, _fit(body, width - cell_len(tag)) + tag)]


def _fit(text: str, width: int | None) -> str:
    if width is None or cell_len(text) < width:
        return text
    if width < 2:
        return ""
    return set_cell_size(text, width - 2) + "…"
