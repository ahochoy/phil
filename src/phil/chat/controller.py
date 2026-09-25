import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from phil.agents.invoke import AgentContext
from phil.chat.approval import effective_test_cmd, git_policy_note, launch_problems, test_cmd_differs, test_cmd_problem
from phil.chat.overview import repo_overview
from phil.chat.planning import Planner, intake
from phil.chat.session import ChatSession
from phil.chat.snapshot import export_tree
from phil.config import ConfigError, PhilConfig, load_config
from phil.contracts import Goal
from phil.repo import RepoInfo, resolve_repo
from phil.run.launch import prepare_run
from phil.store.paths import ProjectPaths
from phil.ui.plan_view import render_goal, render_plan
from phil.ui.runs_view import render_runs

MAX_QUESTION_ROUNDS = 2
HELP = "Type a goal to plan it. Commands: /runs, /help, /quit (or Ctrl-D)."


@dataclass
class ChatIO:
    ask: Callable[[str], str | None]
    spawn: Callable[[Path, str, str], object]


class ChatController:
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

    def run(self) -> None:
        try:
            self._loop()
        finally:
            self._remove_snapshots()

    def _loop(self) -> None:
        while True:
            try:
                raw = self.io.ask("you › ")
            except KeyboardInterrupt:
                # Ctrl-C at the idle prompt: stay in the chat rather than exiting it.
                self.console.print("[phil.muted]Cancelled.[/]")
                continue
            if raw is None:
                return
            text = raw.strip()
            if not text:
                continue
            try:
                if text.startswith("/"):
                    self.session.user(raw, stage="command")
                    if not self._command(text):
                        return
                else:
                    self.session.user(raw, stage="goal")
                    self._goal(text)
            except KeyboardInterrupt:
                self.console.print("[phil.muted]Cancelled.[/]")
            except _Ended:
                return
            except Exception as exc:  # agent, provider, or command failure: report and keep the chat alive
                self._safe_note("error", error=f"{type(exc).__name__}: {exc}")
                self.console.print(f"[phil.error]Phil couldn't finish that: {escape(str(exc))}[/]")
                self.console.print(f"[phil.muted]Details: {escape(str(self.session.dir))}[/]")

    def _snapshot(self) -> Path:
        """Export the base commit's tracked files for the architect (never the live working tree)."""
        sha = self._explicit_base_sha or resolve_repo(self.info.root).head_sha
        return export_tree(self.info.root, sha, self.session.dir / "tree" / sha[:12])

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

    def _ask(self, prompt: str, *, stage: str) -> str:
        """Ask, record the raw answer in the transcript, and return it stripped."""
        answer = self.io.ask(prompt)
        if answer is None:
            raise _Ended()
        self.session.user(answer, stage=stage)
        return answer.strip()

    def _intake(self, message: str, previous: Goal | None = None, answers: list[str] | None = None) -> Goal:
        self._intake_calls += 1
        goal = intake(
            self.ctx, message, overview=self.overview, previous=previous, answers=answers or [], call=self._intake_calls
        )
        self.session.contract("goal", goal)
        return goal

    def _goal(self, text: str) -> None:
        goal = self._intake(text)
        rounds = 0
        while goal.open_questions and rounds < MAX_QUESTION_ROUNDS:
            rounds += 1
            for n, question in enumerate(goal.open_questions, 1):
                self.console.print(f"[phil.agent]{n}. {escape(question)}[/]")
            answer = self._ask("answers (or 'go' to plan anyway) › ", stage="answers")
            if answer.lower() == "go":
                break
            goal = self._intake(text, previous=goal, answers=[answer])
        if goal.open_questions:
            self.console.print(f"[phil.muted]Planning with open questions: {len(goal.open_questions)}[/]")
        render_goal(self.console, goal)
        self.console.print("[phil.muted]Planning…[/]")
        draft = self.planner.draft(goal, self._snapshot())
        self._record_draft(draft)
        self._approve(goal, draft)

    def _record_draft(self, draft) -> None:
        self.session.contract("plan", draft.plan)
        self.session.contract("critique", draft.critique)

    def _approve(self, goal: Goal, draft) -> None:
        while True:
            problem = test_cmd_problem(draft.plan, self.config)
            differs = test_cmd_differs(draft.plan, self.config)
            note = problem or (
                f"plan test command differs from phil.toml's ({self.config.project.test_cmd})" if differs else None
            )
            render_plan(
                self.console,
                draft,
                test_cmd=effective_test_cmd(draft.plan, self.config),
                test_cmd_note=note,
                git_note=git_policy_note(self.config),
            )
            answer = self._ask("Approve? [y / edit / n] › ", stage="approval")
            choice = answer.lower()
            if choice in ("y", "yes"):
                # Errors tell the user to edit phil.toml, so re-read it rather than trusting the chat-start copy.
                try:
                    self.config = load_config(self.info.root)
                except ConfigError as exc:
                    self.console.print(f"[phil.error]{escape(str(exc))}[/]")
                    continue
                problems = launch_problems(draft.plan, self.config)
                if problems:
                    for item in problems:
                        self.console.print(f"[phil.error]{escape(item)}.[/]")
                    self.console.print(
                        "[phil.muted]Fix phil.toml and answer y again, or use edit to change the plan.[/]"
                    )
                    continue
                self._start(draft, answer)
                return
            if choice == "edit":
                feedback = self._ask("What should change? › ", stage="edit")
                if not feedback:
                    continue
                self.console.print("[phil.muted]Revising…[/]")
                draft = self.planner.revise(goal, draft, feedback, self._snapshot())
                self._record_draft(draft)
                continue
            if choice in ("n", "no"):
                self._safe_note("rejected", plan_version=draft.version)
                self.console.print("Plan dropped.")
                return
            self.console.print("Answer y, edit, or n.")

    def _start(self, draft, answer: str) -> None:
        plan = draft.plan.model_copy(update={"test_cmd": effective_test_cmd(draft.plan, self.config)})
        try:
            base_sha = self._explicit_base_sha or resolve_repo(self.info.root).head_sha
            record = prepare_run(self.info, plan, base_sha)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._safe_note("start_failed", plan_version=draft.version, answer=answer, error=error)
            raise  # reported by the loop's error handler
        self._safe_note("approved", plan_version=draft.version, answer=answer, run_id=record.run_id)
        run_id = escape(record.run_id)
        try:
            self.io.spawn(self.info.root, record.run_id, "start")
        except (Exception, KeyboardInterrupt) as exc:
            self._safe_note("spawn_failed", run_id=record.run_id, error=f"{type(exc).__name__}: {exc}")
            self.console.print(
                f"[phil.error]Run {run_id} was created but its worker didn't start: {escape(str(exc))}. "
                f"Start it with `phil resume {run_id}`.[/]"
            )
            return
        self._safe_note("run_started", run_id=record.run_id)
        self.console.print(
            f"Run [phil.id]{run_id}[/] started in the background from {escape(base_sha[:7])}. "
            f"Follow it with `phil attach {run_id}`."
        )


class _Ended(Exception):
    """The user closed input (Ctrl-D) mid-conversation."""
