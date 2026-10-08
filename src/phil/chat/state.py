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
class LiveStep:
    task: str | None
    role: str
    summary: str
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
    live: LiveStep | None = None  # the running tool, shown on the live row above the input
    repo: str = ""
    branch: str = ""
    model: tuple[str, str] | None = None  # (tier label, short name)
    tokens: int | None = None
    run_cost: tuple[float, str] | None = None  # (cost_usd, cost_source): the run's cost
    budget_usd: float = 0.0


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

    def set_live(self, live: LiveStep | None) -> None:
        self._update(live=live)

    def set_paused(self, paused: bool) -> None:
        self._update(paused=paused)

    def set_cancelling(self, cancelling: bool) -> None:
        self._update(cancelling=cancelling)

    def set_cost(self, cost: float, source: str) -> None:
        self._update(cost=(cost, source))

    def set_parked(self, parked: int) -> None:
        self._update(parked=parked)

    def set_place(self, repo: str, branch: str) -> None:
        self._update(repo=repo, branch=branch)

    def set_model(self, model: tuple[str, str] | None) -> None:
        self._update(model=model)

    def set_run_usage(self, tokens: int | None, run_cost: tuple[float, str] | None) -> None:
        self._update(tokens=tokens, run_cost=run_cost)

    def set_budget(self, budget_usd: float) -> None:
        self._update(budget_usd=budget_usd)

    def add_btw(self, delta: int) -> None:
        with self._lock:
            self._view = replace(self._view, btw_pending=max(0, self._view.btw_pending + delta))

    def view(self) -> ToolbarView:
        with self._lock:
            return self._view
