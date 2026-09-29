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
}  # /btw has no step: in-flight /btw questions show as `/btw ×N`


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
        parts.append((-1, format_cost(*view.cost)))  # dropped first on a narrow terminal
    if width is not None:
        limit = width - 1
        while len(parts) > 1 and cell_len(SEPARATOR.join(text for _, text in parts)) > limit:
            parts.remove(min(parts, key=lambda part: part[0]))
    return _fit(SEPARATOR.join(text for _, text in parts), width)


def _fit(text: str, width: int | None) -> str:
    if width is None or cell_len(text) < width:
        return text
    if width < 2:
        return ""
    return set_cell_size(text, width - 2) + "…"
