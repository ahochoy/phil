from phil.chat.state import ToolbarView

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SEPARATOR = "  │  "
STEP_LABELS = {
    "intake": "Understanding the goal",
    "snapshot": "Reading the repo",
    "architect": "Architect drafting",
    "revise": "Architect revising",
    "critic": "Critic reviewing",
    "btw": "Answering /btw",
}


def elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def render_toolbar(view: ToolbarView, now: float) -> str:
    parts: list[str] = []
    if view.step:
        frame = SPINNER[int((now - view.step_started) * 8) % len(SPINNER)]
        label = STEP_LABELS.get(view.step, view.step)
        segment = f"{frame} {label} · {elapsed(now - view.step_started)}"
        if view.cancelling:
            segment += " (cancelling…)"
        parts.append(segment)
    if view.run:
        run = view.run
        parts.append(
            f"{run.run_id} · {run.keyword} {run.tasks_done}/{run.tasks_total} · "
            f"{run.node or 'starting'} · {elapsed(now - run.started)}"
        )
    if view.paused and view.run:
        parts.append(f"⏸ {view.run.run_id} needs you (/answer)")
    if view.btw_pending and view.step != "btw":
        parts.append(f"/btw ×{view.btw_pending}")
    return SEPARATOR.join(parts) if parts else "Phil · type a goal, or /help"
