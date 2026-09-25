import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from phil.agents.invoke import AgentContext
from phil.chat.approval import effective_test_cmd, git_policy_note, test_cmd_differs, test_cmd_problem
from phil.chat.overview import repo_overview
from phil.chat.planning import Planner, intake
from phil.chat.session import ChatSession
from phil.config import RUN_ROLES, PhilConfig
from phil.contracts import Goal
from phil.repo import RepoInfo
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
        self.base_sha = base_sha or info.head_sha
        self.session = session or ChatSession.create(ProjectPaths(info.slug))
        extra = {"sleep": sleep} if sleep is not None else {}
        self.ctx = AgentContext(
            config=config, conn=conn, layer="chat", artifacts=self.session.artifacts, factory=factory, **extra
        )
        self.overview = repo_overview(info.root)
        self.planner = Planner(self.ctx, info.root, self.overview)
        self._intake_calls = 0

    def run(self) -> None:
        while True:
            text = self.io.ask("you › ")
            if text is None:
                return
            text = text.strip()
            if not text:
                continue
            if text.startswith("/"):
                if not self._command(text):
                    return
                continue
            try:
                self._goal(text)
            except KeyboardInterrupt:
                self.console.print("[phil.muted]Cancelled.[/]")
            except _Ended:
                return
            except Exception as exc:  # agent or provider failure: report and keep the chat alive
                self.session.note("error", error=f"{type(exc).__name__}: {exc}")
                self.console.print(f"[phil.error]Phil couldn't finish that: {escape(str(exc))}[/]")
                self.console.print(f"[phil.muted]Details: {escape(str(self.session.dir))}[/]")

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

    def _ask(self, prompt: str) -> str:
        answer = self.io.ask(prompt)
        if answer is None:
            raise _Ended()
        return answer.strip()

    def _intake(self, message: str, previous: Goal | None = None, answers: list[str] | None = None) -> Goal:
        self._intake_calls += 1
        goal = intake(
            self.ctx, message, overview=self.overview, previous=previous, answers=answers or [], call=self._intake_calls
        )
        self.session.contract("goal", goal)
        return goal

    def _goal(self, text: str) -> None:
        self.session.user(text, stage="goal")
        goal = self._intake(text)
        rounds = 0
        while goal.open_questions and rounds < MAX_QUESTION_ROUNDS:
            rounds += 1
            for n, question in enumerate(goal.open_questions, 1):
                self.console.print(f"[phil.agent]{n}. {escape(question)}[/]")
            answer = self._ask("answers (or 'go' to plan anyway) › ")
            if answer.lower() == "go":
                break
            self.session.user(answer, stage="answers")
            goal = self._intake(text, previous=goal, answers=[answer])
        render_goal(self.console, goal)
        self.console.print("[phil.muted]Planning…[/]")
        draft = self.planner.draft(goal)
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
            answer = self._ask("Approve? [y / edit / n] › ")
            self.session.user(answer, stage="approval")
            choice = answer.lower()
            if choice in ("y", "yes"):
                missing = self.config.missing_models(RUN_ROLES)
                if missing:
                    self.console.print(
                        f"[phil.error]phil.toml sets no model for: {escape(', '.join(missing))}. "
                        "Add them under [models] before starting a run.[/]"
                    )
                    continue
                if problem:
                    self.console.print(f"[phil.error]{escape(problem)}. Use edit to fix the plan.[/]")
                    continue
                self._start(draft, answer)
                return
            if choice == "edit":
                feedback = self._ask("What should change? › ")
                if not feedback:
                    continue
                self.session.user(feedback, stage="edit")
                self.console.print("[phil.muted]Revising…[/]")
                draft = self.planner.revise(goal, draft, feedback)
                self._record_draft(draft)
                continue
            if choice in ("n", "no"):
                self.session.note("rejected", plan_version=draft.version)
                self.console.print("Plan dropped.")
                return
            self.console.print("Answer y, edit, or n.")

    def _start(self, draft, answer: str) -> None:
        plan = draft.plan.model_copy(update={"test_cmd": effective_test_cmd(draft.plan, self.config)})
        self.session.note("approved", plan_version=draft.version, answer=answer)
        record = prepare_run(self.info, plan, self.base_sha)
        self.io.spawn(self.info.root, record.run_id, "start")
        self.session.note("run_started", run_id=record.run_id)
        run_id = escape(record.run_id)
        self.console.print(
            f"Run [phil.id]{run_id}[/] started in the background. Follow it with `phil attach {run_id}`."
        )


class _Ended(Exception):
    """The user closed input (Ctrl-D) mid-conversation."""
