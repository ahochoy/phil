import queue
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from phil.agents.invoke import AgentContext
from phil.chat.approval import effective_test_cmd, git_policy_note, launch_problems, test_cmd_differs, test_cmd_problem
from phil.chat.btw import ask_btw
from phil.chat.events import ChatEvent
from phil.chat.overview import repo_overview
from phil.chat.planning import Planner, PlanDraft, intake
from phil.chat.session import ChatSession
from phil.chat.snapshot import export_tree
from phil.chat.state import ChatState, RunView
from phil.chat.watcher import RunWatcher
from phil.config import ConfigError, PhilConfig, load_config
from phil.contracts import Goal, Plan, PlanCritique, RunStatus
from phil.repo import RepoInfo, resolve_repo
from phil.run.launch import is_worker_alive, prepare_run, worker_starting
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.store.telemetry import run_totals
from phil.ui.brief_view import render_brief
from phil.ui.plan_view import _clip, render_goal, render_plan
from phil.ui.runs_view import render_runs

WAKE = object()  # ChatIO.ask returns this when a background event interrupted the prompt
MAX_QUESTION_ROUNDS = 2
HELP = (
    "Type a goal to plan it. Commands: /runs, /btw <question> (ask while work continues), "
    "/answer (a paused run's question), /resume (a failed or stopped run), /help, /quit (or Ctrl-D)."
)
PROMPTS = {
    "questions": "answers (or 'go' to plan anyway) › ",
    "approval": "Approve? [y / edit / n] › ",
    "edit": "What should change? › ",
    "confirm_replace": "Replace the current goal? [y / n] › ",
    "hint": "Hint for the retry (optional) › ",
}
# The transcript's `stage` label for a non-command input typed at each chat stage (4a's labels).
TRANSCRIPT_STAGES = {"idle": "goal", "intake": "goal", "planning": "goal", "running": "goal", "questions": "answers"}
GOAL_JOB_STAGES = ("intake", "planning")
RUN_STAGES = ("running", "paused", "hint")  # the chat's run is in progress
RUN_EVENTS = ("run_progress", "run_paused", "run_resumed", "run_done", "worker_lost", "watch_error")
RECENT_EVENTS = 10  # run events a /btw answer sees


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
    ) -> None:
        self.info, self.config, self.conn, self.console, self.io = info, config, conn, console, io
        # `conn` belongs to the main thread; each job opens its own connection to this database.
        self._db_path = ProjectPaths(info.slug).db_path
        # None means "resolve HEAD when a run is actually started" (_start), not at chat start,
        # so a long-running chat starts its run from the commit current at approval time.
        self._explicit_base_sha = base_sha
        self.session = session or ChatSession.create(ProjectPaths(info.slug))
        extra = {"sleep": sleep} if sleep is not None else {}
        self.ctx = AgentContext(
            config=config, conn=conn, layer="chat", artifacts=self.session.artifacts, factory=factory, **extra
        )
        self.overview = repo_overview(info.root)
        self.planner = Planner(self.ctx, self.overview)
        self._intake_calls = 0
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
    ) -> None:
        """Run `fn` through io.submit; it reports only by posting `kind` (or `failed`) events.

        `fn` gets the job's own AgentContext: a SQLite connection can't cross threads, so each job opens
        one on its thread (agent telemetry and parking write through it) and closes it when done.
        Goal jobs carry the current goal generation; side jobs (/btw) pass -1 so they're never stale.
        """
        generation = self._generation if generation is None else generation

        def work() -> None:
            conn = None
            try:
                conn = connect(self._db_path)
                event = ChatEvent(kind, fn(replace(self.ctx, conn=conn)), generation)
            except Exception as exc:
                event = ChatEvent(failed, {"job": kind, "error": f"{type(exc).__name__}: {exc}"}, generation)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            # Clear the step before posting: once posted, the main loop may start the next job and its step.
            self._step(None, generation)
            self.post(event)

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
            self._loop()
        finally:
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
            self._replacement = text
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
            self.console.print("[phil.muted]Keeping the current goal.[/]")
            self._set_stage(self._parent_stage)
        elif self.stage in ("paused", "hint"):
            self._set_stage("running")
            self.console.print("[phil.muted]Answer it later with /answer.[/]")
        else:
            self.console.print("[phil.muted]Cancelled.[/]")

    # --- goal → questions → plan -----------------------------------------------------------------

    def _reset_goal(self) -> None:
        self._goal, self._goal_text, self._rounds, self._draft, self._revising = None, "", 0, None, False

    def _begin_goal(self, text: str) -> None:
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
        self._set_stage("intake")
        self._intake_job(text)

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
        self._plan(goal)

    def _answers(self, text: str) -> None:
        if not text:
            return
        if text.lower() == "go":
            self._plan(self._goal)
            return
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
            self._begin_goal(self._replacement)
        else:
            self.console.print("[phil.muted]Keeping the current goal.[/]")
            self._set_stage(self._parent_stage)
        self._replacement = ""

    # --- approval and start ----------------------------------------------------------------------

    def _show_plan(self, draft: PlanDraft) -> None:
        problem = test_cmd_problem(draft.plan, self.config)
        differs = test_cmd_differs(draft.plan, self.config)
        note = problem or (
            f"plan test command differs from phil.toml's ({self.config.project.test_cmd})" if differs else None
        )
        self._shown_test_cmd = effective_test_cmd(draft.plan, self.config)
        render_plan(
            self.console,
            draft,
            test_cmd=self._shown_test_cmd,
            test_cmd_note=note,
            git_note=git_policy_note(self.config),
        )

    def _approval(self, answer: str) -> None:
        draft = self._draft
        choice = answer.lower()
        if choice in ("y", "yes"):
            # Errors tell the user to edit phil.toml, so re-read it rather than trusting the chat-start copy.
            try:
                self.config = load_config(self.info.root)
            except ConfigError as exc:
                self.console.print(f"[phil.error]{escape(str(exc))}[/]")
                self._show_plan(draft)
                return
            problems = launch_problems(draft.plan, self.config)
            if problems:
                for item in problems:
                    self.console.print(f"[phil.error]{escape(item)}.[/]")
                self.console.print("[phil.muted]Fix phil.toml and answer y again, or use edit to change the plan.[/]")
                self._show_plan(draft)
                return
            if effective_test_cmd(draft.plan, self.config) != self._shown_test_cmd:
                # phil.toml changed the test command since the plan was shown: show what would run first.
                self.console.print("[phil.warn]The test command changed in phil.toml. Review it and answer again.[/]")
                self._show_plan(draft)
                return
            self._start(draft, answer)
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

    def _start(self, draft: PlanDraft, answer: str) -> None:
        plan = draft.plan.model_copy(update={"test_cmd": effective_test_cmd(draft.plan, self.config)})
        try:
            base_sha = self._explicit_base_sha or resolve_repo(self.info.root).head_sha
            record = prepare_run(self.info, plan, base_sha, chat_id=self.session.id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._safe_note("start_failed", plan_version=draft.version, answer=answer, error=error)
            self._reset_goal()
            self._set_stage("idle")
            raise  # reported by the loop's error handler
        self._safe_note("approved", plan_version=draft.version, answer=answer, run_id=record.run_id)
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
        if state not in ("failed", "stopped"):  # failed/stopped keep the run id for /resume
            self._done_seen = True
            self._run_id = None
            self._reset_goal()  # the chat takes its next goal
        self._set_stage("idle")
        self._run_notice(run_id, state, data)

    def _run_notice(self, run_id: str, state: str, data: dict) -> None:
        rid = escape(run_id)
        summary = f"[phil.muted]Summary: {escape(str(data.get('summary', '')))}[/]"
        attention = data.get("needs_attention")
        if state in ("failed", "stopped"):
            detail = f": {escape(attention)}" if attention else "."
            self.console.print(f"[phil.warn]Run {rid} {escape(state)}{detail}[/]")
            self.console.print("Continue it with /resume.")
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
            return {"brief": brief, "question": question}

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
        render_brief(self.console, brief)

    def _on_btw_failed(self, data: dict) -> None:
        self.state.add_btw(-1)
        self._safe_note("btw_failed", error=data["error"])
        self.console.print(f"[phil.error]/btw failed: {escape(data['error'])}[/]")

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
        values = {key: _count(counters.get(key, 0)) for key in ("intake", "btw", "planner_calls", "planner_version")}
        bad = bad or None in values.values()
        values = {key: value or 0 for key, value in values.items()}
        self._restore_base(saved)
        self._intake_calls = max(self._intake_calls, values["intake"])
        self._btw_calls = max(self._btw_calls, values["btw"])
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
