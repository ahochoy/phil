import logging
import queue
import shutil
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from phil.agents.invoke import AgentContext
from phil.chat.answer import ask_answer
from phil.chat.approval import (
    effective_test_cmd,
    git_policy_note,
    launch_problems,
    terminated,
    test_cmd_differs,
    test_cmd_problem,
)
from phil.chat.btw import ask_btw
from phil.chat.events import ChatEvent
from phil.chat.overview import repo_overview
from phil.chat.planning import Planner, PlanDraft, intake
from phil.chat.session import ChatSession
from phil.chat.snapshot import export_tree, export_worktree
from phil.chat.state import ChatState, RunView
from phil.chat.watcher import RunWatcher
from phil.config import ConfigError, PhilConfig, load_config
from phil.contracts import Goal, Plan, PlanCritique, Ref, RunStatus
from phil.contracts.routing import Answer
from phil.publish import publisher as publishing
from phil.publish.service import PrChange, PublishRefused, change_line, publish_run, sweep_prs
from phil.repo import RepoInfo, resolve_repo
from phil.routing import Route, decide, parse_override, route_state
from phil.routing.classify import classify
from phil.run.launch import is_worker_alive, prepare_run, worker_starting
from phil.store.db import connect
from phil.store.events import run_events, test_cmd_changed_line
from phil.store.paths import ProjectPaths
from phil.store.parked import open_count, park
from phil.store.runs import get_run, list_runs
from phil.store.telemetry import budget_warning_line, chat_usage, run_totals
from phil.ui.answer_view import render_answer
from phil.ui.brief_view import render_brief
from phil.ui.plan_view import _clip, render_goal, render_plan
from phil.ui.runs_view import render_runs
from phil.ui.show_view import detail_text, render_show, show_refs

logger = logging.getLogger(__name__)  # the chat sends `phil` loggers to its phil.log, never the console

WAKE = object()  # ChatIO.ask returns this when a background event interrupted the prompt
MAX_QUESTION_ROUNDS = 2
HELP = (
    "Type a goal or a question. Prefix with /ask, /quick or /full to choose the path. "
    "Commands: /runs, /btw <question> (ask while work continues), "
    "/answer (a paused run's question), /resume (a failed or stopped run), /show (the chat's run: usage "
    "and numbered details), /more <n> (print detail n), /park <note> (set an idea aside), /help, "
    "/quit (or Ctrl-D)."
)
PROMPTS = {
    "questions": "answers (or 'go' to plan anyway) › ",
    "approval": "Approve? [y / edit / n] › ",
    "edit": "What should change? › ",
    "confirm_replace": "Replace the current goal? [y / n] › ",
    "hint": "Hint for the retry (optional) › ",
    "confirm_pr": "Open a PR? [y / n] › ",
    "confirm_fix": "Fix it? [Enter = plan the fix / n] › ",
}
# The transcript's `stage` label for a non-command input typed at each chat stage (4a's labels).
TRANSCRIPT_STAGES = {
    "idle": "goal", "intake": "goal", "planning": "goal", "running": "goal", "questions": "answers",
    "routing": "goal", "answering": "goal",
}
GOAL_JOB_STAGES = ("routing", "intake", "planning", "answering")
RUN_STAGES = ("running", "paused", "hint")  # the chat's run is in progress
RUN_EVENTS = (
    "run_progress", "run_paused", "run_resumed", "run_done", "worker_lost", "watch_error", "budget_warning",
    "test_cmd_changed",
)
RECENT_EVENTS = 10  # run events a /btw answer sees
NOTICE_REFS = 3  # details a completion notice lists
PR_CHECK_INTERVAL_S = 300  # how often an open chat checks its runs' PRs for merges
PR_JOB_ERROR_PREFIXES = ("PublishRefused: ", "PublishError: ")
NOT_IN_SNAPSHOT = "That detail isn't a file in the repo snapshot; not opening it."
OVERRIDE_USAGE = "Usage: /ask|/quick|/full <message>"
RECENT_TURNS = 8  # chat turns kept for the router and the answerer
CONTEXT_TURNS = 4  # of those, the turns an answer sees
TURN_CHARS = 400
CLASS_LABELS = {
    "question": "Question", "diagnosis": "Diagnosis", "small_operation": "Small operation",
    "simple_change": "Simple change", "focused_fix": "Focused fix", "feature": "Feature", "refactor": "Refactor",
    "design": "Design", "broad_project": "Broad project", "other": "Unclear",
}


@dataclass
class ChatIO:
    ask: Callable[[str], object]  # prompt -> str | None (EOF) | WAKE; may raise KeyboardInterrupt
    spawn: Callable[[Path, str, str, dict | None], object]  # (repo_root, run_id, mode, decision)
    wake: Callable[[], None] = lambda: None  # interrupt a blocked ask (keeps typed text)
    submit: Callable[[Callable[[], None]], object] = lambda job: job()  # run a job; inline by default


class ChatController:
    """One goal per chat: goal → questions → plan → approval → run, driven by input and job events.

    Jobs (intake, architect/critic cycles) run through `io.submit` and only post `ChatEvent`s; the main
    loop drains them between inputs and is the only place that prints.
    """

    def __init__(
        self,
        info: RepoInfo,
        config: PhilConfig,
        conn: sqlite3.Connection,
        console: Console,
        io: ChatIO,
        *,
        factory=None,
        base_sha: str | None = None,
        session: ChatSession | None = None,
        sleep: Callable[[float], None] | None = None,
        watcher_factory: Callable[[str], object] | None = None,
        resume: bool = False,
        worker_alive: Callable[[object], bool] = is_worker_alive,
        worker_starting: Callable[[object], bool] = worker_starting,
        pr_check_interval_s: float = PR_CHECK_INTERVAL_S,
        start_pr_monitor: bool = True,
        config_overrides: Sequence[str] = (),
    ) -> None:
        self.info, self.config, self.conn, self.console, self.io = info, config, conn, console, io
        # The chat's `--set` overrides: applied again whenever the config is reloaded, and kept by its runs.
        self._config_overrides = tuple(config_overrides)
        # `conn` belongs to the main thread; each job opens its own connection to this database.
        self._db_path = ProjectPaths(info.slug).db_path
        # None means "resolve HEAD when a run is actually started" (_start), not at chat start,
        # so a long-running chat starts its run from the commit current at approval time.
        self._explicit_base_sha = base_sha
        self.session = session or ChatSession.create(ProjectPaths(info.slug))
        extra = {"sleep": sleep} if sleep is not None else {}
        self.ctx = AgentContext(
            config=config, conn=conn, layer="chat", artifacts=self.session.artifacts, factory=factory,
            chat_id=self.session.id, **extra
        )  # jobs copy this context (with their own connection), so their telemetry is tagged with the chat
        self.overview = repo_overview(info.root)
        self.planner = Planner(self.ctx, self.overview)
        self._intake_calls = 0
        self._route_calls = 0
        self._answer_calls = 0
        # The last few turns ("you: …" / "phil: …"), for the router and the answerer.
        self._recent: deque[str] = deque(maxlen=RECENT_TURNS)
        self._route: Route | None = None  # how the current goal was routed
        self._clarifications: list[str] = []  # the user's answers to intake's questions for this goal
        self._prior_turns: list[str] = []  # the chat turns before this goal's message
        self._fix_offer: tuple[str, str] | None = None  # (question, diagnosis) the confirm_fix question is about
        self.state = ChatState()
        self.events: queue.Queue[ChatEvent] = queue.Queue()
        self.stage = "idle"
        self._parent_stage = "idle"  # where edit / confirm_replace return to
        self._generation = 0
        # Guards generation changes and job step writes together, so a stale job can't set the step
        # after the main thread moved on (check-then-set under one lock).
        self._step_lock = threading.Lock()
        self._goal: Goal | None = None
        self._goal_text = ""
        self._rounds = 0
        self._draft: PlanDraft | None = None
        self._shown_test_cmd: str | None = None  # the effective test command in the last plan render
        self._revising = False
        self._run_id: str | None = None
        self._base_sha: str | None = None
        self._done_seen = False
        self._replacement = ""
        self._replacement_forced: str | None = None  # a replacement typed with /ask, /quick or /full
        self._held: list[ChatEvent] = []  # goal-job events that arrived during confirm_replace
        self._watcher_factory = watcher_factory or (
            lambda run_id: RunWatcher(ProjectPaths(info.slug), run_id, self.post)
        )
        self._watcher = None  # follows the chat's run; a daemon thread (the worker is independent)
        self._pause: dict | None = None  # the pending escalation: {"summary", "options", ...}
        # The pause was answered and a resume worker spawned; the question comes back if the worker
        # exits without taking the run (the watcher re-posts the same escalation).
        self._answer_sent = False
        self._worker_alive, self._worker_starting = worker_alive, worker_starting
        self._lost = False  # the watcher saw the worker stop responding
        self._btw_calls = 0
        self._resume = resume
        self._write_warned = False  # a failed state/transcript write was reported
        self._last_refs: list[Ref] = []  # what /more <n> expands: the last /show, completion notice or /btw details
        # /btw details are model-written paths: they're opened only inside that answer's repo snapshot
        # (`_refs_tree`, None if it had none), never from the live tree or anywhere else on disk.
        self._refs_from_btw = False
        self._refs_tree: Path | None = None
        self._pr_offer: str | None = None  # the completed run the confirm_pr question is about
        self._pr_step_generation: int | None = None  # the generation whose toolbar shows "Opening PR"
        # The PR monitor: a daemon timer thread that only submits `pr_changes` jobs (never prints and
        # never touches `self.conn`); `_pr_stop` ends it and `_pr_busy` skips a tick while a job runs.
        self._pr_interval, self._start_pr_monitor = pr_check_interval_s, start_pr_monitor
        self._pr_stop = threading.Event()
        self._pr_busy = threading.Event()
        self._pr_thread: threading.Thread | None = None

    # --- events and jobs -------------------------------------------------------------------------

    def post(self, event: ChatEvent) -> None:
        """Thread-safe: queue an event for the main loop and wake a blocked prompt."""
        self.events.put(event)
        self.io.wake()

    def _job(
        self,
        kind: str,
        fn: Callable[[AgentContext], dict],
        *,
        generation: int | None = None,
        failed: str = "job_failed",
        failed_data: dict | None = None,
        after: Callable[[], None] | None = None,
    ) -> None:
        """Run `fn` through io.submit; it reports only by posting `kind` (or `failed`) events.

        `fn` gets the job's own AgentContext: a SQLite connection can't cross threads, so each job opens
        one on its thread (agent telemetry and parking write through it) and closes it when done.
        Goal jobs carry the current goal generation; side jobs (/btw) pass -1 so they're never stale.
        `failed_data` is added to a failure event; `after` runs on the job's thread once it has posted.
        """
        generation = self._generation if generation is None else generation

        def work() -> None:
            try:
                conn = None
                try:
                    conn = connect(self._db_path)
                    event = ChatEvent(kind, fn(replace(self.ctx, conn=conn)), generation)
                except Exception as exc:
                    data = {**(failed_data or {}), "job": kind, "error": f"{type(exc).__name__}: {exc}"}
                    event = ChatEvent(failed, data, generation)
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                # Clear the step before posting: once posted, the main loop may start the next job and its step.
                self._step(None, generation)
                self.post(event)
            finally:
                if after is not None:
                    after()

        self.io.submit(work)

    def _step(self, step: str | None, generation: int) -> None:
        """Toolbar step for a job; a replaced or cancelled goal's job can't overwrite the current one."""
        with self._step_lock:
            if generation == self._generation:
                self.state.set_step(step, time.time())

    def _next_generation(self) -> None:
        """Start a new goal generation: in-flight results become stale and the step is cleared."""
        with self._step_lock:
            self._generation += 1
            self.state.set_step(None, time.time())

    def _drain(self) -> None:
        if self.stage != "confirm_replace" and self._held:
            held, self._held = self._held, []
            for event in held:
                self._dispatch(event)
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            if self.stage == "confirm_replace" and event.generation != -1:
                # The goal's result waits until the user decides whether to replace the goal.
                self._held.append(event)
                continue
            self._dispatch(event)

    def _dispatch(self, event: ChatEvent) -> None:
        try:
            self._handle(event)
        except Exception as exc:
            self._report(exc)
            if event.generation != -1:
                # Only a failed goal-job result can strand the goal's stage; a failed /btw answer or
                # run event leaves the questions / edit / approval stage as it was.
                self._recover()
        finally:
            # A finished job or a run progress/done event may have added telemetry.
            self._refresh_cost()

    def _refresh_cost(self) -> None:
        """The chat's running cost on the toolbar (its own agent calls plus its runs'); main thread only."""
        try:
            totals = chat_usage(self.conn, self.session.id)
        except Exception:
            return  # the toolbar keeps the last cost; the chat carries on
        if totals.tokens or totals.cost_usd:
            self.state.set_cost(totals.cost_usd, totals.cost_source)

    def _recover(self) -> None:
        """After a failed goal-job handler, return to a stage the user can act on."""
        if self.stage in (*RUN_STAGES, "confirm_replace"):
            return  # the run (or the pending question) is unaffected
        if self._draft is not None:
            self._revising = False
            self._set_stage("approval")
        else:
            self._next_generation()  # an in-flight job's result can't land in idle
            self._reset_goal()
            self._set_stage("idle")

    def _handle(self, event: ChatEvent) -> None:
        if event.generation not in (-1, self._generation):
            self.state.set_cancelling(False)
            return
        if event.kind in RUN_EVENTS and self._run_id is None:
            return  # from a watcher whose run the chat no longer follows
        handler = getattr(self, f"_on_{event.kind}", None)
        if handler:
            handler(event.data)

    # --- stage and persistence -------------------------------------------------------------------

    def _set_stage(self, stage: str) -> None:
        self.stage = stage
        self.state.set_stage(stage)
        self._save()

    def _prompt(self) -> str:
        if self.stage == "paused" and self._pause is not None:
            return f"{self._pause.get('summary', '')} — {' / '.join(self._options())} › "
        return PROMPTS.get(self.stage, "you › ")

    def _save(self) -> None:
        """Write state.json so a reopened chat can rebuild itself; a failed write never interrupts the chat."""
        try:
            stage = self.stage
            if stage == "confirm_replace":
                stage = self._parent_stage
            elif stage in ("confirm_pr", "confirm_fix"):
                stage = "idle"  # the question isn't asked again (`phil pr` opens a PR later)
            elif stage == "edit" or (stage == "planning" and self._revising and self._draft is not None):
                stage = "approval"  # a reopened chat returns to the draft being revised
            draft = self._draft
            planner_calls, planner_version = self.planner.counters()
            self.session.save_state(
                {
                    "stage": stage,
                    "goal": self._goal.model_dump(mode="json") if self._goal else None,
                    "plan": draft.plan.model_dump(mode="json") if draft else None,
                    "critique": draft.critique.model_dump(mode="json") if draft else None,
                    "version": draft.version if draft else None,
                    "run_id": self._run_id,
                    "base_sha": self._base_sha,
                    "explicit_base_sha": self._explicit_base_sha,  # `--base`; None follows HEAD
                    "done_seen": self._done_seen,
                    # Agent-call numbers name the chat's artifacts; a reopened chat continues from them.
                    "counters": {
                        "intake": self._intake_calls,
                        "planner_calls": planner_calls,
                        "planner_version": planner_version,
                        "btw": self._btw_calls,
                        "route": self._route_calls,
                        "answer": self._answer_calls,
                    },
                }
            )
        except Exception as exc:
            self._write_failed(exc)

    def _write_failed(self, exc: Exception) -> None:
        """A state or transcript write failed: say so once per chat, never interrupt it."""
        if self._write_warned:
            return
        self._write_warned = True
        try:
            self.console.print(
                f"[phil.muted]Couldn't save the chat state: {escape(f'{type(exc).__name__}: {exc}')}[/]"
            )
        except Exception:
            pass

    # --- the loop --------------------------------------------------------------------------------

    def run(self) -> None:
        try:
            if self._resume:
                self._reopen()
            self._refresh_cost()
            self._refresh_parked()
            if self._start_pr_monitor:
                self._start_pr_monitor_thread()
            self._loop()
        finally:
            self._stop_pr_monitor()
            self._stop_watcher()
            self._remove_snapshots()
            self._save()

    def _loop(self) -> None:
        while True:
            try:
                self._drain()
                raw = self.io.ask(self._prompt())
                if raw is WAKE:
                    continue
                if raw is None or not self._input(raw):
                    self._closing()
                    return
            except KeyboardInterrupt:
                self._interrupt()
            except Exception as exc:  # agent, provider, or command failure: report and keep the chat alive
                self._report(exc)

    def _report(self, exc: BaseException) -> None:
        self._failed(f"{type(exc).__name__}: {exc}")

    def _failed(self, error: str) -> None:
        self._safe_note("error", error=error)
        self.console.print(f"[phil.error]Phil couldn't finish that: {escape(error)}[/]")
        self.console.print(f"[phil.muted]Details: {escape(str(self.session.dir))}[/]")

    def _input(self, raw: str) -> bool:
        """Record the raw input with its stage, then route it. Returns False to end the chat."""
        text = raw.strip()
        forced, rest = parse_override(text)
        if forced is not None:
            self._forced_input(raw, forced, rest)
            return True
        if text.startswith("/"):
            self.session.user(raw, stage="command")
            return self._command(text)
        stage = self.stage
        if not text and stage in ("idle", "running", "paused", *GOAL_JOB_STAGES):
            return True
        self.session.user(raw, stage=TRANSCRIPT_STAGES.get(stage, stage))
        if stage == "idle":
            self._begin_goal(text)
        elif stage in GOAL_JOB_STAGES:
            self._replacement, self._replacement_forced = text, None
            self._parent_stage = stage
            self._set_stage("confirm_replace")
        elif stage == "questions":
            self._answers(text)
        elif stage == "approval":
            self._approval(text)
        elif stage == "edit":
            self._edit(text)
        elif stage == "confirm_replace":
            self._confirm_replace(text)
        elif stage == "confirm_pr":
            self._confirm_pr(text)
        elif stage == "confirm_fix":
            self._confirm_fix(text)
        elif stage == "paused":
            self._pause_answer(text)
        elif stage == "hint":
            self._hint(text)
        elif stage == "running":
            self.console.print(
                f"This chat is following run [phil.id]{escape(self._run_id or '')}[/]. "
                "Use /btw to ask about it, or start another goal in a new window."
            )
        return True

    def _command(self, text: str) -> bool:
        command = text.split()[0]
        if command in ("/quit", "/exit"):
            return False
        if command == "/help":
            self.console.print(escape(HELP))
        elif command == "/runs":
            render_runs(self.console, self.conn)
        elif command == "/btw":
            self._btw(text[len(command):].strip())
        elif command == "/answer":
            self._answer_command()
        elif command == "/resume":
            self._resume_command()
        elif command == "/show":
            self._show_command()
        elif command == "/more":
            self._more_command(text[len(command):].strip())
        elif command == "/park":
            self._park_command(text[len(command):].strip())
        else:
            self.console.print(f"Unknown command {escape(command)}. Try /help.")
        return True

    def _interrupt(self) -> None:
        """Ctrl-C: cancel an in-flight goal, back out of a sub-prompt, or just acknowledge."""
        if self.stage == "planning" and self._revising and self._draft is not None:
            self._next_generation()  # the revision is dropped when it lands
            self.state.set_cancelling(True)
            self._revising = False
            self._set_stage("approval")
            self.console.print("[phil.muted]Cancelled the revision.[/]")
            self._show_plan(self._draft)
        elif self.stage in (*GOAL_JOB_STAGES, "questions"):
            self._next_generation()  # an in-flight result is dropped when it lands
            self.state.set_cancelling(True)
            self._reset_goal()
            self._set_stage("idle")
            self.console.print("[phil.muted]Cancelled the current goal.[/]")
        elif self.stage == "edit":
            self._set_stage("approval")
        elif self.stage == "confirm_replace":
            self._replacement, self._replacement_forced = "", None
            self.console.print("[phil.muted]Keeping the current goal.[/]")
            self._set_stage(self._parent_stage)
        elif self.stage in ("paused", "hint"):
            self._set_stage("running")
            self.console.print("[phil.muted]Answer it later with /answer.[/]")
        elif self.stage == "confirm_pr":
            self._decline_pr()
        elif self.stage == "confirm_fix":
            self._fix_offer = None
            self._set_stage("idle")
        else:
            self.console.print("[phil.muted]Cancelled.[/]")

    # --- goal → questions → plan -----------------------------------------------------------------

    def _reset_goal(self) -> None:
        self._goal, self._goal_text, self._rounds, self._draft, self._revising = None, "", 0, None, False
        self._route, self._clarifications, self._prior_turns = None, [], []

    def _forced_input(self, raw: str, forced: str, rest: str) -> None:
        """A message prefixed with /ask, /quick or /full: a goal whose depth skips the router."""
        if not rest:
            self.session.user(raw, stage="command")
            self.console.print(OVERRIDE_USAGE)
            return
        self.session.user(raw, stage="goal")
        stage = self.stage
        if stage in ("idle", "confirm_fix"):
            self._fix_offer = None
            self._begin_goal(rest, forced=forced)
        elif stage in GOAL_JOB_STAGES:
            self._replacement, self._replacement_forced = rest, forced
            self._parent_stage = stage
            self._set_stage("confirm_replace")
        elif stage == "confirm_pr":
            self._decline_pr()
            self._begin_goal(rest, forced=forced)
        elif stage in RUN_STAGES:
            self.console.print(
                f"This chat is following run [phil.id]{escape(self._run_id or '')}[/]. "
                "Use /btw to ask about it, or start another goal in a new window."
            )
        else:
            self.console.print("Finish the current goal first, or press Ctrl-C to cancel it.")

    def _begin_goal(self, text: str, forced: str | None = None, source: str = "forced") -> None:
        """Start a goal: routed, or at the `forced` depth (`source` says who forced it: the user's
        prefix, "forced", or an accepted fix offer, "fix_offer")."""
        if self._run_id is not None:
            # A new goal replaces a failed or stopped run the chat was offering to /resume.
            record = get_run(self.conn, self._run_id)
            rid = escape(self._run_id)
            state = escape(record.state) if record else "is"
            self.console.print(
                f"[phil.muted]Run {rid} is left as {state}; `phil resume {rid}` still continues it.[/]"
            )
            self._forget_run()
        self._next_generation()
        self._reset_goal()
        self._goal_text = text
        chat = self._prior_turns = list(self._recent)  # the turns before this message
        self._recent.append(f"you: {text[:TURN_CHARS]}")
        if forced is not None:
            self._routed(Route(forced, source, "forced", text, None))
            return
        self._set_stage("routing")
        self._route_job(text, chat)

    # --- routing and answers ---------------------------------------------------------------------

    def _route_job(self, text: str, chat: list[str]) -> None:
        self._route_calls += 1
        call, generation = self._route_calls, self._generation
        self._step("routing", generation)
        root = self.info.root

        def fn(ctx: AgentContext) -> dict:
            return {"judgement": classify(ctx, route_state(text, chat, root), call=call), "text": text}

        self._job("route_ready", fn)

    def _on_route_ready(self, data: dict) -> None:
        judgement = data["judgement"]
        depth, reason = decide(
            judgement,
            confidence_threshold=self.config.routing.confidence_threshold,
            detail_threshold=self.config.routing.detail_threshold,
        )
        source = judgement.source if depth is not None else "intake"
        if judgement is not None and judgement.fallback_reason:
            self.console.print(
                f"[phil.muted]Router unavailable ({escape(judgement.fallback_reason)}); using your low model.[/]"
            )
        self._routed(Route(depth, source, reason, data["text"], judgement))

    def _routed(self, route: Route) -> None:
        """Record the route, say which path the message takes, and start it."""
        self._route = route
        j = route.judgement
        self._safe_note(
            "route",
            task_class=j.task_class if j else None,
            depth=route.depth,
            source=route.source,
            reason=route.reason,
            confidence=j.confidence if j else None,
            needs_detail=j.needs_detail if j else None,
            latency_ms=j.latency_ms if j else None,
            fallback_reason=j.fallback_reason if j else None,
        )
        self.console.print(f"[phil.muted]{escape(self._status_line(route))}[/]")
        if route.depth == "answer":
            self._answer_job(route.text)
        else:
            self._set_stage("intake")
            self._intake_job(route.text)

    def _status_line(self, route: Route) -> str:
        if route.source == "fix_offer":
            return "Fix · planning"  # M3b: the quick path
        if route.source == "forced":
            # Until M3b's quick engine, a forced quick goal is planned like any other.
            return "Forced: quick · planning" if route.depth == "quick" else f"Forced: {route.depth} path"
        if route.reason == "needs_detail":
            return "Unclear request · asking first"
        if route.depth is None:
            return "Not sure how big this is · intake decides"
        label = CLASS_LABELS.get(route.judgement.task_class, "Unclear")
        if route.depth == "answer":
            return f"{label} · answering (/full to plan a change instead)"
        if route.depth == "quick":
            return f"{label} · planning  (/quick and /full force a path)"  # M3b: the quick path
        return f"{label} · full plan"

    def _answer_job(self, question: str) -> None:
        self._set_stage("answering")
        self._answer_calls += 1
        call, generation = self._answer_calls, self._generation
        self._step("answering", generation)
        context = "\n".join(self._prior_turns[-CONTEXT_TURNS:])
        root, overview = self.info.root, self.overview
        tree = self.session.dir / "tree" / f"answer{call}"

        def fn(ctx: AgentContext) -> dict:
            # The answerer reads a working-tree snapshot, never the live root: uncommitted edits are
            # visible, ignored files (.env, .venv, local keys) are not. Removed once answered; the
            # chat's end removes all of tree/ in any case.
            try:
                snapshot = export_worktree(root, tree)
                answer = ask_answer(ctx, question, root=snapshot, overview=overview, context=context, call=call)
            finally:
                shutil.rmtree(tree, ignore_errors=True)
            return {"answer": answer, "question": question}

        self._job("answer_ready", fn, failed="answer_failed")

    def _on_answer_ready(self, data: dict) -> None:
        answer: Answer = data["answer"]
        judgement = self._route.judgement if self._route else None
        task_class = judgement.task_class if judgement else None
        self._safe_note("answer", text=answer.text, files=answer.files, task_class=task_class)
        render_answer(self.console, answer)
        self._recent.append(f"phil: {answer.text[:TURN_CHARS]}")
        self._reset_goal()
        if task_class == "diagnosis":
            self._fix_offer = (data["question"], answer.text)
            self._set_stage("confirm_fix")
        else:
            self._set_stage("idle")

    def _on_answer_failed(self, data: dict) -> None:
        self._safe_note("error", error=data["error"])
        self.console.print(f"[phil.error]Phil couldn't answer that: {escape(data['error'])}[/]")
        self._reset_goal()
        self._set_stage("idle")

    def _confirm_fix(self, text: str) -> None:
        offer, self._fix_offer = self._fix_offer, None
        choice = text.lower()
        if choice in ("", "y", "yes") and offer is not None:
            question, diagnosis = offer
            self._begin_goal(f"{question}\n\nDiagnosis so far:\n{diagnosis}", forced="quick", source="fix_offer")
        elif choice in ("", "y", "yes", "n", "no"):
            self._set_stage("idle")
        else:
            self._begin_goal(text)  # another message is a new goal

    def _intake_job(self, message: str, previous: Goal | None = None, answers: list[str] | None = None) -> None:
        self._intake_calls += 1
        call, generation = self._intake_calls, self._generation
        self._step("intake", generation)

        def fn(ctx: AgentContext) -> dict:
            goal = intake(
                ctx, message, overview=self.overview, previous=previous, answers=answers or [], call=call
            )
            return {"goal": goal}

        self._job("goal_ready", fn)

    def _on_goal_ready(self, data: dict) -> None:
        goal: Goal = data["goal"]
        self.session.contract("goal", goal)
        self._goal = goal
        if goal.open_questions and self._rounds < MAX_QUESTION_ROUNDS:
            self._rounds += 1
            for n, question in enumerate(goal.open_questions, 1):
                self.console.print(f"[phil.agent]{n}. {escape(_clip(question))}[/]")
            self._set_stage("questions")
            return
        intake_decides = self._route is not None and self._route.depth is None
        if intake_decides and goal.depth == "answer" and not goal.open_questions:
            # The router left the depth to intake, which chose an answer; it sees the user's clarifications.
            question = self._goal_text
            if self._clarifications:
                question += "\n\nClarification: " + "\n".join(self._clarifications)
            self._answer_job(question)
            return
        self._plan(goal)

    def _answers(self, text: str) -> None:
        if not text:
            return
        if text.lower() == "go":
            self._plan(self._goal)
            return
        self._clarifications.append(text)
        self._recent.append(f"you: {text[:TURN_CHARS]}")
        self._set_stage("intake")
        self._intake_job(self._goal_text, previous=self._goal, answers=[text])

    def _plan(self, goal: Goal) -> None:
        if goal.open_questions:
            self.console.print(f"[phil.muted]Planning with open questions: {len(goal.open_questions)}[/]")
        render_goal(self.console, goal)
        self.console.print("[phil.muted]Planning…[/]")
        self._revising = False
        self._set_stage("planning")
        generation = self._generation

        def fn(ctx: AgentContext) -> dict:
            self._step("snapshot", generation)
            tree = self._snapshot(generation)
            on_step = lambda step: self._step(step, generation)  # noqa: E731
            return {"draft": self.planner.draft(goal, tree, on_step=on_step, ctx=ctx)}

        self._job("plan_ready", fn)

    def _on_plan_ready(self, data: dict) -> None:
        draft: PlanDraft = data["draft"]
        self._record_draft(draft)
        self._draft = draft
        self._revising = False
        self._set_stage("approval")
        self._show_plan(draft)

    def _on_job_failed(self, data: dict) -> None:
        self._failed(data["error"])
        if self._revising and self._draft is not None:
            self._revising = False
            self._set_stage("approval")  # keep the previous draft
        else:
            self._reset_goal()
            self._set_stage("idle")

    def _confirm_replace(self, text: str) -> None:
        if text.lower() in ("y", "yes"):
            self._begin_goal(self._replacement, forced=self._replacement_forced)
        else:
            self.console.print("[phil.muted]Keeping the current goal.[/]")
            self._set_stage(self._parent_stage)
        self._replacement, self._replacement_forced = "", None

    # --- approval and start ----------------------------------------------------------------------

    def _show_plan(self, draft: PlanDraft) -> None:
        root = self._detection_root(draft.plan)
        problem = test_cmd_problem(draft.plan, self.config, root)
        differs = test_cmd_differs(draft.plan, self.config)
        origin = self.config.sources.get("project.test_cmd")  # the file that set [project] test_cmd
        note = problem or (
            f"plan test command differs from {origin or 'your config'}'s ({self.config.project.test_cmd})"
            if differs
            else None
        )
        self._shown_test_cmd, source = effective_test_cmd(draft.plan, self.config, root)
        render_plan(
            self.console,
            draft,
            test_cmd=self._shown_test_cmd,
            test_cmd_source=source,
            test_cmd_origin=origin,
            test_cmd_note=note,
            git_note=git_policy_note(self.config),
        )

    def _detection_root(self, plan: Plan) -> Path | None:
        """Where a missing test command is detected from: the base commit's snapshot, which is what
        the run will see. None when the plan or phil.toml already sets one, or the export fails."""
        if plan.test_cmd or self.config.project.test_cmd:
            return None
        try:
            return self._snapshot(self._generation)
        except Exception:
            logger.warning("couldn't export the snapshot to detect a test command", exc_info=True)
            return None

    def _approval(self, answer: str) -> None:
        draft = self._draft
        choice = answer.lower()
        if choice in ("y", "yes"):
            # Errors tell the user to edit their config, so re-read it rather than trusting the chat-start copy.
            try:
                self.config = load_config(self.info.root, overrides=self._config_overrides)
            except ConfigError as exc:
                self.console.print(f"[phil.error]{escape(str(exc))}[/]")
                self._show_plan(draft)
                return
            root = self._detection_root(draft.plan)
            # Check commands' paths must stay inside the tree: the base commit's snapshot when it was
            # exported for detection, else the repo itself.
            problems = launch_problems(draft.plan, self.config, root, check_root=root or self.info.root)
            if problems:
                for item in problems:
                    self.console.print(f"[phil.error]{escape(terminated(item))}[/]")
                self.console.print("[phil.muted]Fix your config and answer y again, or use edit to change the plan.[/]")
                self._show_plan(draft)
                return
            if effective_test_cmd(draft.plan, self.config, root)[0] != self._shown_test_cmd:
                # The config changed the test command since the plan was shown: show what would run first.
                self.console.print("[phil.warn]The test command changed in your config. Review it and answer again.[/]")
                self._show_plan(draft)
                return
            self._start(draft, answer, self._shown_test_cmd)
        elif choice == "edit":
            self._parent_stage = "approval"
            self._set_stage("edit")
        elif choice in ("n", "no"):
            self._safe_note("rejected", plan_version=draft.version)
            self.console.print("Plan dropped.")
            self._reset_goal()
            self._set_stage("idle")
        else:
            self.console.print("Answer y, edit, or n.")

    def _edit(self, feedback: str) -> None:
        if not feedback:
            self._set_stage("approval")
            return
        goal, draft = self._goal, self._draft
        self.console.print("[phil.muted]Revising…[/]")
        self._revising = True
        self._set_stage("planning")
        generation = self._generation

        def fn(ctx: AgentContext) -> dict:
            self._step("snapshot", generation)
            tree = self._snapshot(generation)
            revised = self.planner.revise(
                goal, draft, feedback, tree, on_step=lambda step: self._step(step, generation), ctx=ctx
            )
            return {"draft": revised}

        self._job("plan_ready", fn)

    def _start(self, draft: PlanDraft, answer: str, test_cmd: str | None) -> None:
        # The run keeps the command the user approved (the plan's, phil.toml's or the detected one).
        plan = draft.plan.model_copy(update={"test_cmd": test_cmd})
        try:
            base_sha = self._explicit_base_sha or resolve_repo(self.info.root).head_sha
            record = prepare_run(
                self.info, plan, base_sha, chat_id=self.session.id, overrides=self._config_overrides
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._safe_note("start_failed", plan_version=draft.version, answer=answer, error=error)
            self._reset_goal()
            self._set_stage("idle")
            raise  # reported by the loop's error handler
        depth = (self._route.depth if self._route else None) or (self._goal.depth if self._goal else None)
        self._safe_note("approved", plan_version=draft.version, answer=answer, run_id=record.run_id, depth=depth)
        self._recent.append(f"phil: planned {plan.keyword}")
        run_id = escape(record.run_id)
        try:
            self.io.spawn(self.info.root, record.run_id, "start", None)
        except (Exception, KeyboardInterrupt) as exc:
            self._safe_note("spawn_failed", run_id=record.run_id, error=f"{type(exc).__name__}: {exc}")
            self.console.print(
                f"[phil.error]Run {run_id} was created but its worker didn't start: {escape(str(exc))}. "
                f"Start it with `phil resume {run_id}`.[/]"
            )
            self._reset_goal()
            self._set_stage("idle")
            return
        self._safe_note("run_started", run_id=record.run_id)
        self._run_id, self._base_sha, self._done_seen = record.run_id, base_sha, False
        self._lost, self._pause = False, None
        self._set_stage("running")
        self.console.print(
            f"Run [phil.id]{run_id}[/] started in the background from {escape(base_sha[:7])}. "
            f"This chat follows it; from another window use `phil attach {run_id}`."
        )
        self._follow()

    # --- 4a helpers ------------------------------------------------------------------------------

    def _snapshot(self, generation: int) -> Path:
        """Export the base commit's tracked files for the architect (never the live working tree).

        Each goal generation gets its own directory, so a stale job re-exporting can't remove a tree
        another job is reading. All of `tree/` is removed when the chat ends.
        """
        sha = self._explicit_base_sha or resolve_repo(self.info.root).head_sha
        return export_tree(self.info.root, sha, self.session.dir / "tree" / f"{sha[:12]}-g{generation}")

    def _remove_snapshots(self) -> None:
        try:
            shutil.rmtree(self.session.dir / "tree", ignore_errors=True)
        except Exception:
            pass

    def _safe_note(self, kind: str, **data: object) -> None:
        """Record a transcript note without letting a logging failure crash the REPL."""
        try:
            self.session.note(kind, **data)
        except Exception as exc:
            self._write_failed(exc)

    def _record_draft(self, draft: PlanDraft) -> None:
        self.session.contract("plan", draft.plan)
        self.session.contract("critique", draft.critique)

    # --- following the run -----------------------------------------------------------------------

    def _follow(self) -> None:
        """Start a watcher for the chat's run; its events arrive through the queue."""
        self._stop_watcher()
        run_id = self._run_id
        try:
            watcher = self._watcher_factory(run_id)
            watcher.start()
        except Exception as exc:
            self.console.print(
                f"[phil.warn]Couldn't follow run {escape(run_id)} here ({escape(f'{type(exc).__name__}: {exc}')}); "
                f"use `phil attach {escape(run_id)}`.[/]"
            )
            return
        self._watcher = watcher

    def _stop_watcher(self) -> None:
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                pass

    def _forget_run(self) -> None:
        self._stop_watcher()
        self._run_id, self._lost, self._pause, self._answer_sent = None, False, None, False
        self.state.set_run(None)
        self.state.set_paused(False)

    def _closing(self) -> None:
        """EOF or /quit: the worker is independent, so say how to come back to a run still working."""
        if self._run_id is not None and self.stage in RUN_STAGES:
            self.console.print(
                f"Run [phil.id]{escape(self._run_id)}[/] keeps working. "
                f"Reopen this chat with `phil --resume {escape(self.session.id)}`."
            )

    def _options(self) -> list[str]:
        return list((self._pause or {}).get("options") or ["abort"])

    def _on_run_progress(self, data: dict) -> None:
        self.state.set_run(
            RunView(
                run_id=self._run_id,
                keyword=data.get("keyword") or "",
                node=data.get("node"),
                tasks_done=data.get("tasks_done", 0),
                tasks_total=data.get("tasks_total", 0),
                started=data.get("started", 0.0),
            )
        )

    def _on_budget_warning(self, data: dict) -> None:
        line = budget_warning_line(self._run_id, **data)
        self.console.print(f"[phil.warn]{escape(line)}[/]")

    def _on_test_cmd_changed(self, data: dict) -> None:
        self.console.print(f"[phil.warn]{escape(test_cmd_changed_line(str(data.get('cmd'))))}[/]")

    def _on_run_paused(self, data: dict) -> None:
        escalation = data["escalation"]
        if self._answer_sent:
            log = ProjectPaths(self.info.slug).run_dir(self._run_id) / "logs" / "worker.log"
            self.console.print(
                f"[phil.warn]The worker exited without resuming the run; see {escape(str(log))}[/]"
            )
        self._pause, self._answer_sent = escalation, False
        self.state.set_paused(True)
        self._safe_note("run_paused", run_id=self._run_id, escalation=escalation)
        self.console.print(
            f"[phil.warn]⏸ {escape(self._run_id)} needs you: {escape(str(escalation.get('summary', '')))}[/]"
        )
        if self.stage in RUN_STAGES:
            self._set_stage("paused")

    def _on_run_resumed(self, data: dict) -> None:
        # Answered here (the resume worker took the run) or elsewhere (drop the pending question).
        self._pause, self._answer_sent = None, False
        self.state.set_paused(False)
        self.console.print(f"[phil.muted]{escape(self._run_id)} resumed.[/]")
        if self.stage in ("paused", "hint"):
            self._set_stage("running")

    def _pause_answer(self, text: str) -> None:
        options = self._options()
        choice = {option.lower(): option for option in options}.get(text.lower())
        if choice is None:
            self.console.print(f"Answer one of: {escape(', '.join(options))}")
            return
        if choice == "retry":
            self._set_stage("hint")
            return
        self._resume_run({"action": choice})

    def _hint(self, text: str) -> None:
        self._resume_run({"action": "retry"} | ({"hint": text} if text else {}))

    def _worker_active(self, record) -> bool:
        events = run_events(ProjectPaths(self.info.slug), record.run_id)
        return self._worker_alive(record) or self._worker_starting(events)

    def _resume_run(self, decision: dict) -> None:
        """Answer the pause: spawn a resume worker, unless the run already moved on."""
        run_id = self._run_id
        record = get_run(self.conn, run_id)
        if record is None or record.state != "escalated" or self._worker_active(record):
            self._pause, self._answer_sent = None, False
            self.state.set_paused(False)
            self._set_stage("running")
            self.console.print("[phil.muted]The run moved on; nothing to answer.[/]")
            return
        self._safe_note("pause_answered", run_id=run_id, decision=decision)
        try:
            self.io.spawn(self.info.root, run_id, "resume", decision)
        except (Exception, KeyboardInterrupt) as exc:
            self._safe_note("spawn_failed", run_id=run_id, error=f"{type(exc).__name__}: {exc}")
            self._set_stage("running")  # the pause stays pending: /answer asks it again
            self.console.print(
                f"[phil.error]The resume worker for {escape(run_id)} didn't start: {escape(str(exc))}. "
                "Try /answer again.[/]"
            )
            return
        # Keep the question until the worker takes the run: if it exits first, the watcher re-posts it.
        self._answer_sent = True
        self.state.set_paused(False)
        self._set_stage("running")
        rearm = getattr(self._watcher, "rearm", None)
        if rearm is not None:
            rearm()
        self.console.print(f"Resuming [phil.id]{escape(run_id)}[/] with {escape(decision['action'])}.")

    def _on_run_done(self, data: dict) -> None:
        # Settle the chat first, so a notice that fails to print can't leave it following a finished run.
        self._stop_watcher()
        self.state.set_run(None)
        self.state.set_paused(False)
        self._pause, self._lost, self._answer_sent = None, False, False
        run_id, state = self._run_id, data.get("state", "")
        self._safe_note("run_done", run_id=run_id, state=state)
        self._refresh_parked()  # workers may have parked items during the run
        if state not in ("failed", "stopped"):  # failed/stopped keep the run id for /resume
            self._done_seen = True
            self._run_id = None
            self._reset_goal()  # the chat takes its next goal
        self._set_stage("idle")
        self._run_notice(run_id, state, data)
        if state == "completed":
            self._offer_pr(run_id)

    def _run_notice(self, run_id: str, state: str, data: dict) -> None:
        rid = escape(run_id)
        summary = f"[phil.muted]Summary: {escape(str(data.get('summary', '')))}[/]"
        attention = data.get("needs_attention")
        if state in ("failed", "stopped"):
            detail = f": {escape(attention)}" if attention else "."
            self.console.print(f"[phil.warn]Run {rid} {escape(state)}{detail}[/]")
            self.console.print("Continue it with /resume.")
            self._notice_refs(run_id)
            return
        if state == "completed":
            tokens, cost = data.get("tokens", 0), data.get("cost_usd", 0.0)
            done, total = data.get("tasks_done", 0), data.get("tasks_total", 0)
            self.console.print(
                f"[phil.gate.pass]✓ Run {rid} completed · {done}/{total} tasks · {tokens:,} tokens · ${cost:.2f}[/]"
            )
            if attention:
                self.console.print(f"[phil.warn]Open issues: {escape(attention)}[/]")
            self.console.print(f"[phil.muted]Review it: phil diff {rid}[/]")
            self.console.print(summary)
        else:
            self.console.print(f"Run {rid} was {escape(state)}.")
            self.console.print(summary)
        self._notice_refs(run_id)

    def _notice_refs(self, run_id: str) -> None:
        """List the run's first few details, numbered for /more."""
        refs = show_refs(ProjectPaths(self.info.slug), run_id)[:NOTICE_REFS]
        if not refs:
            return
        self._set_refs(refs)
        self.console.print("[phil.muted]Details (/more <n> prints one, /show lists all):[/]")
        for number, ref in enumerate(refs, start=1):
            self.console.print(f"  [phil.id]{number}[/] {escape(ref.label)}")

    def _on_worker_lost(self, data: dict) -> None:
        self._lost = True
        self.console.print(
            f"[phil.warn]The worker for {escape(self._run_id)} stopped responding. Continue it with /resume.[/]"
        )

    def _on_watch_error(self, data: dict) -> None:
        # The watcher keeps polling and posts this once per failure streak; it recovers on its own.
        rid = escape(self._run_id)
        self.console.print(
            f"[phil.muted]Live updates for {rid} are failing ({escape(str(data.get('error', '')))}); retrying. "
            f"`phil attach {rid}` also works.[/]"
        )

    def _answer_command(self) -> None:
        if self._pause is not None and not self._answer_sent and self.stage in RUN_STAGES:
            self._set_stage("paused")
        else:
            self.console.print("Nothing needs you right now.")

    def _resume_command(self) -> None:
        """Continue the chat's failed or stopped run (or one whose worker was lost)."""
        run_id = self._run_id
        record = get_run(self.conn, run_id) if run_id and self.stage in ("idle", "running") else None
        resumable = record is not None and (
            record.state in ("failed", "stopped") or (self._lost and record.state in ("running", "pending"))
        )
        if not resumable or self._worker_active(record):
            self.console.print("Nothing to resume.")
            return
        self._safe_note("resume", run_id=run_id)
        self.io.spawn(self.info.root, run_id, "continue", None)  # a failure is reported by the loop
        self._lost, self._done_seen = False, False
        self._set_stage("running")
        self.console.print(f"Continuing [phil.id]{escape(run_id)}[/] from its last checkpoint.")
        self._follow()

    # --- /show, /more, /park ----------------------------------------------------------------------

    def _set_refs(self, refs: list[Ref], *, btw_tree: Path | None = None, from_btw: bool = False) -> None:
        self._last_refs, self._refs_from_btw, self._refs_tree = list(refs), from_btw, btw_tree

    def _refresh_parked(self) -> None:
        try:
            self.state.set_parked(open_count(self.conn))
        except Exception:
            pass  # the toolbar keeps the last count

    def _chat_run(self) -> str | None:
        """The run this chat follows, else the last run it started."""
        if self._run_id is not None:
            return self._run_id
        mine = [record for record in list_runs(self.conn) if record.chat_id == self.session.id]
        return mine[0].run_id if mine else None

    def _show_command(self) -> None:
        run_id = self._chat_run()
        if run_id is None or get_run(self.conn, run_id) is None:
            self.console.print("No run to show yet.")
            return
        self._set_refs(render_show(self.console, self.conn, ProjectPaths(self.info.slug), run_id))

    def _more_command(self, arg: str) -> None:
        if not arg.isdigit():
            self.console.print("Usage: /more <n>")
            return
        n = int(arg)
        if not 1 <= n <= len(self._last_refs):
            self.console.print(f"No detail #{n}. Use /show to list them.")
            return
        ref = self._last_refs[n - 1]
        if self._refs_from_btw:
            path = _inside_snapshot(self._refs_tree, ref.path)
            if path is None:
                self.console.print(NOT_IN_SNAPSHOT)
                return
        else:
            path = Path(ref.path)  # Phil's own listing: files under the run's directory
        try:
            text = detail_text(path)
        except (OSError, UnicodeDecodeError) as exc:
            self.console.print(f"[phil.error]Couldn't read {escape(str(path))}: {escape(type(exc).__name__)}[/]")
            return
        self.console.print(Text(text))  # plain text: never markup

    def _park_command(self, note: str) -> None:
        if not note:
            self.console.print("Usage: /park <note>")
            return
        item = park(
            self.conn,
            raised_by="user",
            note=note,
            why_not_now="parked from chat",
            source=Ref(label=f"chat {self.session.id}", path=str(self.session.dir)),
            run_id=self._run_id,
        )
        self._safe_note("parked", id=item.id, note=note)
        self._refresh_parked()
        self.console.print(f"Parked [phil.id]{escape(item.id)}[/].")

    # --- /btw ------------------------------------------------------------------------------------

    def _btw(self, question: str) -> None:
        if not question:
            self.console.print("Usage: /btw <question>")
            return
        # Context is gathered here, on the main thread; the job only exports the snapshot and asks.
        goal = self._goal
        plan = self._draft.plan if self._draft else None
        run, recent = self._run_context()
        pending = self._pause.get("summary") if self._pause else None
        base = self._base_sha if self._run_id else None
        self._btw_calls += 1
        call = self._btw_calls
        self.state.add_btw(1)

        def fn(ctx: AgentContext) -> dict:
            tree = None
            if goal is not None:
                sha = base or self._explicit_base_sha or resolve_repo(self.info.root).head_sha
                tree = export_tree(self.info.root, sha, self.session.dir / "tree" / f"{sha[:12]}-btw{call}")
            brief = ask_btw(
                ctx, question, goal=goal, plan=plan, run=run, recent_events=recent,
                pending_question=pending, tree=tree, call=call,
            )
            return {"brief": brief, "question": question, "tree": tree}

        self._job("btw_answer", fn, generation=-1, failed="btw_failed")

    def _run_context(self) -> tuple[RunStatus | None, list[str]]:
        if self._run_id is None:
            return None, []
        record = get_run(self.conn, self._run_id)
        if record is None:
            return None, []
        tokens, cost = run_totals(self.conn, record.run_id)
        run = RunStatus(
            run_id=record.run_id, state=record.state, tasks_done=record.tasks_done, tasks_total=record.tasks_total,
            current_node=record.current_node, tokens=tokens, cost_usd=cost, needs_attention=record.needs_attention,
        )
        try:
            events, _ = run_events(ProjectPaths(self.info.slug), record.run_id).read()
        except Exception:
            events = []
        return run, [_short_event(event) for event in events[-RECENT_EVENTS:]]

    def _on_btw_answer(self, data: dict) -> None:
        self.state.add_btw(-1)
        brief = data["brief"]
        self._safe_note("btw", question=data.get("question"), brief=brief.model_dump(mode="json"))
        self.console.print("[phil.muted]btw ›[/]")
        render_brief(self.console, brief, numbered=True)
        if brief.details:
            self._set_refs(brief.details, btw_tree=data.get("tree"), from_btw=True)

    def _on_btw_failed(self, data: dict) -> None:
        self.state.add_btw(-1)
        self._safe_note("btw_failed", error=data["error"])
        self.console.print(f"[phil.error]/btw failed: {escape(data['error'])}[/]")

    # --- pull requests ---------------------------------------------------------------------------

    def _offer_pr(self, run_id: str) -> None:
        """After a completed run's notice: ask to open its PR, if it has a base branch and no PR yet."""
        record = get_run(self.conn, run_id)
        if record is None or record.base_branch is None or record.pr_url is not None:
            return
        self.console.print(f"Open a PR for [phil.id]{escape(run_id)}[/] → {escape(record.base_branch)}?")
        self._pr_offer = run_id
        self._set_stage("confirm_pr")

    def _confirm_pr(self, text: str) -> None:
        choice = text.lower()
        if choice in ("y", "yes"):
            run_id, self._pr_offer = self._pr_offer, None
            self._set_stage("idle")  # the chat stays usable while the PR opens
            self._open_pr(run_id)
            return
        self._decline_pr()
        if choice not in ("", "n", "no"):
            self._begin_goal(text)  # a goal typed at the question isn't lost

    def _decline_pr(self) -> None:
        run_id, self._pr_offer = self._pr_offer, None
        self.console.print(f"No PR. `phil pr {escape(run_id or '')}` opens one later.")
        self._set_stage("idle")

    def _open_pr(self, run_id: str) -> None:
        self.console.print(f"Opening a PR for [phil.id]{escape(run_id)}[/]…")
        generation = self._generation
        self._pr_step_generation = generation
        self._step("Opening PR", generation)
        info = self.info

        def fn(ctx: AgentContext) -> dict:
            record = get_run(ctx.conn, run_id)
            if record is None:
                raise PublishRefused(f"{run_id} no longer exists")
            record = publish_run(info, ctx.conn, record, publishing.make_publisher(info.root))
            return {"run_id": run_id, "number": record.pr_number, "url": record.pr_url}

        self._job("pr_opened", fn, generation=-1, failed="pr_open_failed", failed_data={"run_id": run_id})

    def _clear_pr_step(self) -> None:
        generation, self._pr_step_generation = self._pr_step_generation, None
        if generation is not None:
            self._step(None, generation)  # a newer goal's step is left alone

    def _on_pr_opened(self, data: dict) -> None:
        self._clear_pr_step()
        self._safe_note("pr_opened", run_id=data["run_id"], number=data["number"], url=data["url"])
        self.console.print(f"Opened PR #{data['number']}: {escape(str(data['url']))}")

    def _on_pr_open_failed(self, data: dict) -> None:
        self._clear_pr_step()
        error = str(data.get("error", ""))
        for prefix in PR_JOB_ERROR_PREFIXES:
            error = error.removeprefix(prefix)
        run_id = str(data.get("run_id", ""))
        self._safe_note("pr_open_failed", run_id=run_id, error=error)
        self.console.print(f"[phil.warn]Couldn't open the PR: {escape(error)}[/]")
        self.console.print(f"Fix that, then `phil pr {escape(run_id)}`.")

    def _start_pr_monitor_thread(self) -> None:
        self._pr_stop.clear()
        thread = threading.Thread(target=self._pr_monitor, name="phil-pr-monitor", daemon=True)
        thread.start()
        self._pr_thread = thread

    def _pr_monitor(self) -> None:
        """Timer thread: check PRs now, then every interval, until the chat ends. Only submits jobs."""
        while not self._pr_stop.is_set():
            try:
                self._pr_tick()
            except Exception:
                logger.warning("PR check couldn't start", exc_info=True)
            if self._pr_stop.wait(self._pr_interval):
                return

    def _pr_tick(self) -> None:
        if self._pr_stop.is_set() or self._pr_busy.is_set():
            return  # quitting, or the previous check is still running
        self._pr_busy.set()
        info = self.info

        def fn(ctx: AgentContext) -> dict:
            changes = sweep_prs(info, ctx.conn, publishing.make_publisher(info.root))
            return {"changes": [asdict(change) for change in changes]}

        try:
            self._job("pr_changes", fn, generation=-1, failed="pr_changes_failed", after=self._pr_busy.clear)
        except BaseException:
            self._pr_busy.clear()
            raise

    def _stop_pr_monitor(self) -> None:
        self._pr_stop.set()
        thread, self._pr_thread = self._pr_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _on_pr_changes(self, data: dict) -> None:
        for raw in data.get("changes", []):
            change = PrChange(**raw)
            style = "phil.muted" if change.kind == "merged" else "phil.warn"
            self.console.print(f"[{style}]{escape(change_line(change))}[/]")
        self._refresh_parked()

    def _on_pr_changes_failed(self, data: dict) -> None:
        logger.warning("PR check failed: %s", data.get("error"))  # phil.log only; the next check retries

    # --- reopening -------------------------------------------------------------------------------

    def _reopen(self) -> None:
        """Rebuild the chat from state.json: follow its run, or return to its plan's approval.

        A saved part that doesn't validate (hand-edited or from a broken write) isn't trusted: the
        chat warns, keeps the valid agent-call counters and base, and starts idle.
        """
        saved = self.session.load_state()
        bad = not isinstance(saved, dict)
        saved = saved if not bad else {}
        goal = draft = None
        try:
            goal = Goal.model_validate(saved["goal"]) if saved.get("goal") else None
        except Exception:
            bad = True
        try:
            if saved.get("plan") and saved.get("critique"):
                draft = PlanDraft(
                    plan=Plan.model_validate(saved["plan"]),
                    critique=PlanCritique.model_validate(saved["critique"]),
                    version=_count(saved.get("version")) or 1,
                )
        except Exception:
            bad = True
        run_id, base_sha = saved.get("run_id"), saved.get("base_sha")
        if not all(value is None or isinstance(value, str) for value in (run_id, base_sha)):
            bad, run_id, base_sha = True, None, None
        counters = saved.get("counters") or {}
        if not isinstance(counters, dict):
            bad, counters = True, {}
        values = {
            key: _count(counters.get(key, 0))
            for key in ("intake", "btw", "route", "answer", "planner_calls", "planner_version")
        }
        bad = bad or None in values.values()
        values = {key: value or 0 for key, value in values.items()}
        self._restore_base(saved)
        self._intake_calls = max(self._intake_calls, values["intake"])
        self._btw_calls = max(self._btw_calls, values["btw"])
        self._route_calls = max(self._route_calls, values["route"])
        self._answer_calls = max(self._answer_calls, values["answer"])
        version = max(values["planner_version"], draft.version if draft and not bad else 0)
        self.planner.restore(values["planner_calls"], version)
        self._safe_note("reopened")
        if bad:
            self.console.print(f"[phil.brand]Reopened {escape(self.session.id)}[/].")
            self.console.print(
                "[phil.muted]This chat's saved state couldn't be fully restored; starting fresh.[/]"
            )
            self._reset_goal()
            self._set_stage("idle")
            return
        self._goal, self._draft = goal, draft
        self._goal_text = goal.objective if goal else ""
        self._run_id, self._base_sha = run_id, base_sha
        self._done_seen = bool(saved.get("done_seen", False))
        title = f": {escape(goal.objective)}" if goal else "."
        self.console.print(f"[phil.brand]Reopened {escape(self.session.id)}[/]{title}")
        stage = saved.get("stage", "idle")
        if self._run_id:
            self._set_stage("running")
            self.console.print(f"[phil.muted]Following run {escape(self._run_id)}.[/]")
            self._follow()
        elif stage in ("approval", "planning", "edit") and self._draft is not None:
            self._set_stage("approval")
            self._show_plan(self._draft)
        else:
            self._reset_goal()
            self._set_stage("idle")
            if stage != "idle":
                self.console.print(
                    "The chat was closed before a plan was ready; send the goal again or type a new one."
                )

    def _restore_base(self, saved: dict) -> None:
        """The chat keeps the base it was opened with; a different --base on reopen is ignored."""
        if "explicit_base_sha" not in saved:
            return  # saved before the base was kept: use this session's
        kept = saved["explicit_base_sha"]
        if kept is not None and not isinstance(kept, str):
            return
        given = self._explicit_base_sha
        if given is not None and given != kept:
            label = kept[:7] if kept else "HEAD"
            self.console.print(
                f"[phil.muted]This chat keeps its base {escape(label)}; --base {escape(given[:7])} is ignored.[/]"
            )
        self._explicit_base_sha = kept


def _inside_snapshot(tree: Path | None, raw: str) -> Path | None:
    """A /btw detail path as a file inside its repo snapshot, or None.

    The /btw agent reads the snapshot through deepagents' virtual filesystem, where `/` is the
    snapshot root, so a leading `/` is read as the snapshot root too (`/calc.py` is the
    snapshot's calc.py; `/etc/hosts` is `<snapshot>/etc/hosts`, which doesn't exist). `~`
    paths, `..` escapes and symlinks resolving outside the snapshot are refused."""
    if tree is None or not raw or raw.startswith("~"):
        return None
    relative = Path(raw.lstrip("/"))
    if relative.is_absolute():  # e.g. a Windows drive path
        return None
    try:
        root = tree.resolve(strict=True)
        candidate = (root / relative).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _count(value: object) -> int | None:
    """A saved counter: a non-negative int, else None (bools and strings aren't counters)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _short_event(event: dict) -> str:
    """One run event as a short line for the /btw agent."""
    kind = event.get("kind", "?")
    if kind == "node":
        return f"node {event.get('node')}"
    if kind == "escalation":
        return f"escalation: {(event.get('escalation') or {}).get('summary', '')}"
    if kind == "state":
        note = f" — {event['needs_attention']}" if event.get("needs_attention") else ""
        return f"state {event.get('state')}{note}"
    if kind in ("worker", "spawn"):
        return f"{kind} {event.get('mode')}"
    if kind == "outcome":
        return f"outcome {event.get('status')}"
    return str(kind)
