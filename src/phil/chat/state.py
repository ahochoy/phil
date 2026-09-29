import threading
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RunView:
    run_id: str
    keyword: str
    node: str | None
    tasks_done: int
    tasks_total: int
    started: float  # epoch seconds


@dataclass(frozen=True)
class ToolbarView:
    stage: str = "idle"
    step: str | None = None
    step_started: float = 0.0
    run: RunView | None = None
    paused: bool = False
    btw_pending: int = 0
    cancelling: bool = False
    cost: tuple[float, str] | None = None  # (cost_usd, cost_source): the chat's running cost
    parked: int = 0  # open parked items


class ChatState:
    """What the toolbar shows; written by worker threads and the main loop, read on every redraw."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._view = ToolbarView()

    def _update(self, **fields: object) -> None:
        with self._lock:
            self._view = replace(self._view, **fields)

    def set_stage(self, stage: str) -> None:
        self._update(stage=stage)

    def set_step(self, step: str | None, now: float) -> None:
        self._update(step=step, step_started=now)

    def set_run(self, run: RunView | None) -> None:
        self._update(run=run)

    def set_paused(self, paused: bool) -> None:
        self._update(paused=paused)

    def set_cancelling(self, cancelling: bool) -> None:
        self._update(cancelling=cancelling)

    def set_cost(self, cost: float, source: str) -> None:
        self._update(cost=(cost, source))

    def set_parked(self, parked: int) -> None:
        self._update(parked=parked)

    def add_btw(self, delta: int) -> None:
        with self._lock:
            self._view = replace(self._view, btw_pending=max(0, self._view.btw_pending + delta))

    def view(self) -> ToolbarView:
        with self._lock:
            return self._view
