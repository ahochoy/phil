import queue
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from phil.agents.invoke import AgentContext
from phil.chat.approval import effective_test_cmd, git_policy_note, launch_problems, test_cmd_differs, test_cmd_problem
from phil.chat.events import ChatEvent
from phil.chat.overview import repo_overview
from phil.chat.planning import Planner, PlanDraft, intake
from phil.chat.session import ChatSession
from phil.chat.snapshot import export_tree
from phil.chat.state import ChatState
from phil.config import ConfigError, PhilConfig, load_config
from phil.contracts import Goal
from phil.repo import RepoInfo, resolve_repo
from phil.run.launch import prepare_run
from phil.store.paths import ProjectPaths
from phil.ui.plan_view import _clip, render_goal, render_plan
from phil.ui.runs_view import render_runs

WAKE = object()  # ChatIO.ask returns this when a background event interrupted the prompt
MAX_QUESTION_ROUNDS = 2
HELP = "Type a goal to plan it. Commands: /runs, /help, /quit (or Ctrl-D)."
PROMPTS = {
    "questions": "answers (or 'go' to plan anyway) › ",
    "approval": "Approve? [y / edit / n] › ",
    "edit": "What should change? › ",
    "confirm_replace": "Replace the current goal? [y / n] › ",
}
# The transcript's `stage` label for a non-command input typed at each chat stage (4a's labels).
TRANSCRIPT_STAGES = {"idle": "goal", "intake": "goal", "planning": "goal", "running": "goal", "questions": "answers"}
GOAL_JOB_STAGES = ("intake", "planning")


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
        watcher_factory=None,
    ) -> None:
        self.info, self.config, self.conn, self.console, self.io = info, config, conn, console, io
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
        self._watcher_factory = watcher_factory  # used to follow the run (Task 7)

    # --- events and jobs -------------------------------------------------------------------------

    def post(self, event: ChatEvent) -> None:
        """Thread-safe: queue an event for the main loop and wake a blocked prompt."""
        self.events.put(event)
        self.io.wake()

    def _job(self, kind: str, fn: Callable[[], dict]) -> None:
        """Run `fn` through io.submit; it reports only by posting `kind` (or `job_failed`) events."""
        generation = self._generation

        def work() -> None:
            try:
                event = ChatEvent(kind, fn(), generation)
            except Exception as exc:
                event = ChatEvent("job_failed", {"job": kind, "error": f"{type(exc).__name__}: {exc}"}, generation)
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
            self._recover()

    def _recover(self) -> None:
        """After a failed event handler, return to a stage the user can act on."""
        if self.stage in ("running", "confirm_replace"):
            return  # the run (or the pending question) is unaffected
        if self._draft is not None:
            self._revising = False
            self._set_stage("approval")
        else:
            self._reset_goal()
            self._set_stage("idle")

    def _handle(self, event: ChatEvent) -> None:
        if event.generation not in (-1, self._generation):
            self.state.set_cancelling(False)
            return
        handler = getattr(self, f"_on_{event.kind}", None)
        if handler:
            handler(event.data)

    # --- stage and persistence -------------------------------------------------------------------

    def _set_stage(self, stage: str) -> None:
        self.stage = stage
        self.state.set_stage(stage)
        self._save()

    def _prompt(self) -> str:
        return PROMPTS.get(self.stage, "you › ")

    def _save(self) -> None:
        """Write state.json so a reopened chat can rebuild itself; a failed write never interrupts the chat."""
        try:
            stage = self.stage
            if stage == "confirm_replace":
                stage = self._parent_stage
            elif stage == "edit":
                stage = "approval"
            draft = self._draft
            self.session.save_state(
                {
                    "stage": stage,
                    "goal": self._goal.model_dump(mode="json") if self._goal else None,
                    "plan": draft.plan.model_dump(mode="json") if draft else None,
                    "critique": draft.critique.model_dump(mode="json") if draft else None,
                    "version": draft.version if draft else None,
                    "run_id": self._run_id,
                    "base_sha": self._base_sha,
                    "done_seen": self._done_seen,
                }
            )
        except Exception:
            pass

    # --- the loop --------------------------------------------------------------------------------

    def run(self) -> None:
        try:
            self._loop()
        finally:
            self._remove_snapshots()
            self._save()

    def _loop(self) -> None:
        while True:
            try:
                self._drain()
                raw = self.io.ask(self._prompt())
                if raw is WAKE:
                    continue
                if raw is None:
                    return
                if not self._input(raw):
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
        if not text and stage in ("idle", "running", *GOAL_JOB_STAGES):
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
        elif self.stage in GOAL_JOB_STAGES:
            self._next_generation()  # the in-flight result is dropped when it lands
            self.state.set_cancelling(True)
            self._reset_goal()
            self._set_stage("idle")
            self.console.print("[phil.muted]Cancelled the current goal.[/]")
        elif self.stage == "edit":
            self._set_stage("approval")
        elif self.stage == "confirm_replace":
            self.console.print("[phil.muted]Keeping the current goal.[/]")
            self._set_stage(self._parent_stage)
        else:
            self.console.print("[phil.muted]Cancelled.[/]")

    # --- goal → questions → plan -----------------------------------------------------------------

    def _reset_goal(self) -> None:
        self._goal, self._goal_text, self._rounds, self._draft, self._revising = None, "", 0, None, False

    def _begin_goal(self, text: str) -> None:
        self._next_generation()
        self._reset_goal()
        self._goal_text = text
        self._set_stage("intake")
        self._intake_job(text)

    def _intake_job(self, message: str, previous: Goal | None = None, answers: list[str] | None = None) -> None:
        self._intake_calls += 1
        call, generation = self._intake_calls, self._generation
        self._step("intake", generation)

        def fn() -> dict:
            goal = intake(
                self.ctx, message, overview=self.overview, previous=previous, answers=answers or [], call=call
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

        def fn() -> dict:
            self._step("snapshot", generation)
            tree = self._snapshot(generation)
            return {"draft": self.planner.draft(goal, tree, on_step=lambda step: self._step(step, generation))}

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

        def fn() -> dict:
            self._step("snapshot", generation)
            tree = self._snapshot(generation)
            revised = self.planner.revise(
                goal, draft, feedback, tree, on_step=lambda step: self._step(step, generation)
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
        self._set_stage("running")
        self.console.print(
            f"Run [phil.id]{run_id}[/] started in the background from {escape(base_sha[:7])}. "
            f"Follow it with `phil attach {run_id}`."
        )

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
        except Exception:
            pass

    def _record_draft(self, draft: PlanDraft) -> None:
        self.session.contract("plan", draft.plan)
        self.session.contract("critique", draft.critique)
