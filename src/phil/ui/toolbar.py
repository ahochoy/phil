from rich.cells import cell_len, set_cell_size

from phil.chat.state import ToolbarView
from phil.store.telemetry import format_cost

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SEPARATOR = "  │  "
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


def elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def render_toolbar(view: ToolbarView, now: float, width: int | None = None) -> str:
    """The toolbar line; with `width`, it's fitted to fewer cells than that so it never wraps."""
    parts: list[tuple[int, str]] = []  # (priority: lower is dropped first, text)
    if view.step:
        frame = SPINNER[int((now - view.step_started) * 8) % len(SPINNER)]
        label = STEP_LABELS.get(view.step, view.step)
        segment = f"{frame} {label} · {elapsed(now - view.step_started)}"
        if view.cancelling:
            segment += " (cancelling…)"
        parts.append((2, segment))
    if view.run:
        run = view.run
        parts.append(
            (
                1,
                f"{run.run_id} · {run.keyword} {run.tasks_done}/{run.tasks_total} · "
                f"{run.node or 'starting'} · {elapsed(now - run.started)}",
            )
        )
    if view.paused and view.run:
        parts.append((3, f"⏸ {view.run.run_id} needs you (/answer)"))
    if view.btw_pending:
        parts.append((0, f"/btw ×{view.btw_pending}"))
    if not parts:
        parts.append((4, "Phil · type a goal, or /help"))
    if view.cost is not None:
        parts.append((-1, format_cost(*view.cost)))  # dropped early on a narrow terminal
    if view.parked:
        parts.append((-2, f"{view.parked} parked"))  # dropped first
    if width is not None:
        limit = width - 1
        while len(parts) > 1 and cell_len(SEPARATOR.join(text for _, text in parts)) > limit:
            parts.remove(min(parts, key=lambda part: part[0]))
    return _fit(SEPARATOR.join(text for _, text in parts), width)


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
