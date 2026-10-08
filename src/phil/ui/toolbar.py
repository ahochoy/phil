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


def render_live_row(view: ToolbarView, now: float, width: int | None = None) -> str:
    """The live row above the input: the running tool, else the run's stage; empty without a run."""
    if view.run is None:
        return ""
    if view.live is not None:
        live = view.live
        frame = SPINNER[int((now - live.started) * 8) % len(SPINNER)]
        parts = [p for p in (live.task, live.role, live.summary, elapsed(now - live.started)) if p]
        text = f"{frame} " + " · ".join(parts)
    else:
        node = view.run.node or "starting"
        text = "  " + NODE_LABELS.get(node, node)
    return _fit(text, width)


def _fit(text: str, width: int | None) -> str:
    if width is None or cell_len(text) < width:
        return text
    if width < 2:
        return ""
    return set_cell_size(text, width - 2) + "…"
