import threading
from dataclasses import dataclass, replace

from phil.ui.runs import label_for


@dataclass(frozen=True)
class RunView:
    run_id: str
    keyword: str
    node: str | None
    tasks_done: int
    tasks_total: int
    started: float  # epoch seconds
    name: str | None = None  # the run's display name (spec 2026-10-16 §3); None: show run_id
    title: str | None = None  # the current task's title; None before tasks exist or once all are done


@dataclass(frozen=True)
class LiveStep:
    task: str | None
    role: str
    summary: str
    started: float  # epoch seconds


@dataclass(frozen=True)
class SubAgent:
    seq: int
    description: str
    summary: str | None
    started: float  # epoch seconds


@dataclass(frozen=True)
class SideJob:
    label: str
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
    subs: tuple[SubAgent, ...] = ()  # active sub-agents, shown indented under the live row
    side: tuple[SideJob, ...] = ()  # in-flight /btw questions, shown as their own line
    feed_filter: str | None = None  # the agent the feed is filtered to, if any


class ChatState:
    """What the toolbar shows; written by worker threads and the main loop, read on every redraw."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._view = ToolbarView()
        self.labelled_runs: set[str] = set()  # runs this chat has named in full

    def mention(self, run_id: str, name: str | None) -> str:
        """The run's label for a line printed now: `name (id)` the first time this chat names it,
        then `name` (the ID when it has none)."""
        with self._lock:
            first = run_id not in self.labelled_runs
            self.labelled_runs.add(run_id)
        return label_for(run_id, name, first=first)

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

    def set_subs(self, subs: tuple[SubAgent, ...]) -> None:
        self._update(subs=subs)

    def add_side(self, job: SideJob) -> None:
        with self._lock:
            self._view = replace(self._view, side=(*self._view.side, job))

    def remove_side(self, job: SideJob) -> None:
        with self._lock:
            side = list(self._view.side)
            if job in side:
                side.remove(job)
            self._view = replace(self._view, side=tuple(side))

    def set_feed_filter(self, agent: str | None) -> None:
        self._update(feed_filter=agent)

    def view(self) -> ToolbarView:
        with self._lock:
            return self._view
