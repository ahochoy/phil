import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from phil.agents.invoke import AgentContext, AgentFactory, ContractViolation, invoke_agent
from phil.agents.registry import get_spec
from phil.agents.tools import CommandLog
from phil.config import PhilConfig
from phil.contracts import ImplementInput, Issue, ReviewInput, TesterInput, TestReport
from phil.git import GitError, branch_for
from phil.packets import PacketTooLarge, build_packet
from phil.run.gates import is_test_path, run_tests, snapshot_tests, verify_green, verify_red
from phil.run.state import RunState, issues_to_tasks, load_plan, next_todo, render_summary, with_task_status
from phil.store.artifacts import ArtifactStore, artifact_name
from phil.store.runs import update_run
from phil.store.telemetry import run_totals
from phil.workspace.worktree import WorktreeManager


@dataclass
class RunDeps:
    config: PhilConfig
    conn: sqlite3.Connection
    repo_root: Path
    run_id: str
    worktree: Path
    artifacts: ArtifactStore
    factory: AgentFactory | None = None
    sleep: Callable[[float], None] = time.sleep


class RunEngine:
    def __init__(self, deps: RunDeps) -> None:
        self.deps = deps
        self.worktrees = WorktreeManager(deps.repo_root)

    # --- graph -------------------------------------------------------------

    def build(self, checkpointer: Any) -> Any:
        graph = StateGraph(RunState)
        graph.add_node("setup", self.setup)
        graph.add_node("pick_task", self.pick_task)
        graph.add_node("implement", self.implement)
        graph.add_node("verify", self.verify)
        graph.add_node("commit", self.commit)
        graph.add_node("finish", self.finish)
        graph.add_node("tester", self.tester)
        graph.add_node("tester_task", self.tester_task)
        graph.add_node("review", self.review)
        graph.add_edge(START, "setup")
        graph.add_edge("setup", "pick_task")
        graph.add_conditional_edges("pick_task", self.route_after_pick, ["implement", "tester", "review"])
        graph.add_conditional_edges("review", self.route_after_review, ["pick_task", "finish", "escalate"])
        graph.add_node("escalate", self.escalate)
        graph.add_conditional_edges("implement", self.route_after_implement, ["verify", "escalate"])
        graph.add_conditional_edges("verify", self.route_after_verify, ["implement", "commit", "escalate"])
        graph.add_conditional_edges(
            "escalate",
            self.route_after_escalate,
            ["implement", "verify", "pick_task", "finish", "tester", "tester_task", "review", "commit"],
        )
        graph.add_conditional_edges("commit", self.route_after_commit, ["tester_task", "pick_task", "escalate"])
        graph.add_conditional_edges("tester", self.route_after_tester, ["pick_task", "escalate"])
        graph.add_conditional_edges("tester_task", self.route_after_tester, ["pick_task", "escalate"])
        graph.add_edge("finish", END)
        return graph.compile(checkpointer=checkpointer)

    def route_after_pick(self, state: RunState) -> str:
        if state["task_index"] >= 0:
            return "implement"
        return "review" if state.get("tester_done") else "tester"

    def route_after_verify(self, state: RunState) -> str:
        return {"red_ok": "implement", "green_ok": "commit", "retry": "implement", "escalate": "escalate"}[
            state["verdict"]
        ]

    # --- helpers -----------------------------------------------------------

    def _update_run(self, **fields: object) -> None:
        update_run(self.deps.conn, self.deps.run_id, **fields)

    def _test(self, state: RunState, name: str) -> TestReport:
        return run_tests(
            state["test_cmd"],
            self.deps.worktree,
            shell=self.deps.config.shell,
            artifacts=self.deps.artifacts,
            name=name,
            baseline=state.get("baseline_failures", []),
        )

    def _context(self, state: RunState, log: CommandLog) -> AgentContext:
        return AgentContext(
            config=self.deps.config,
            conn=self.deps.conn,
            layer="run",
            run_id=self.deps.run_id,
            artifacts=self.deps.artifacts,
            workdir=self.deps.worktree,
            factory=self.deps.factory,
            sleep=self.deps.sleep,
            command_log=log,
            extra_allow=tuple(state.get("approved", [])),
        )

    def _budget(self, role: str) -> int:
        return self.deps.config.budget_for(role).max_input_tokens

    def _sign(self) -> bool | None:
        value = self.deps.config.git.sign_commits
        return None if value == "auto" else value

    def _commit(self, message: str, bypass: bool) -> None:
        self.worktrees.commit_all(
            self.deps.worktree,
            message,
            sign=False if bypass else self._sign(),
            run_hooks=False if bypass else self.deps.config.git.run_hooks,
        )

    @staticmethod
    def _first_stderr_line(exc: GitError) -> str:
        return next((line.strip() for line in exc.stderr.splitlines() if line.strip()), "") or str(exc)

    def _budget_escalation(self, state: RunState, node: str) -> dict | None:
        tokens, cost = run_totals(self.deps.conn, self.deps.run_id)
        max_tokens = state.get("budget_limit_tokens") or self.deps.config.run.max_tokens
        max_cost = state.get("budget_limit_cost") or self.deps.config.run.max_cost_usd
        if tokens < max_tokens and cost < max_cost:
            return None
        return {
            "reason": "budget",
            "options": ["continue", "abort"],
            "resume_to": node,
            "summary": f"run used {tokens} tokens (${cost:.2f}); limit {max_tokens} tokens / ${max_cost:.2f}",
        }

    # --- nodes -------------------------------------------------------------

    def setup(self, state: RunState) -> dict:
        plan = load_plan(state)
        if not self.deps.worktree.exists():
            self.worktrees.create(run_id=self.deps.run_id, base_sha=state["base_sha"], path=self.deps.worktree)
        self.deps.artifacts.write_plan(plan)
        baseline = self._test({**state, "baseline_failures": []}, "baseline")
        self._update_run(state="running", current_node="setup", tasks_total=len(plan.tasks))
        return {
            "baseline_failures": baseline.failures,
            "initial_baseline": baseline.failures,
            "base_passed": baseline.passed_count,
            "base_skipped": baseline.skipped_count,
            "status": "running",
        }

    def pick_task(self, state: RunState) -> dict:
        index = next_todo(load_plan(state))
        self._update_run(current_node="pick_task")
        if index is None:
            return {"task_index": -1}
        return {
            "task_index": index,
            "task_base_sha": self.worktrees.head(self.deps.worktree),
            "phase": "red",
            "attempts": 0,
            "last_problems": [],
            "last_report": None,
            "red_snapshot": {},
            "red_tree": "",
            "hint": None,
            "implement_failed": False,
            "denied": [],
            "escalation": None,
        }

    def implement(self, state: RunState) -> dict:
        if (escalation := self._budget_escalation(state, "implement")) is not None:
            return {"escalation": escalation}
        if state["phase"] == "red":
            self.worktrees.reset_to(self.deps.worktree, state["task_base_sha"])
        else:
            self.worktrees.restore_snapshot(self.deps.worktree, state["red_tree"])
        plan = load_plan(state)
        task = plan.tasks[state["task_index"]]
        seq = state.get("call_seq", 0) + 1
        feedback = list(state.get("last_problems", []))
        if state.get("hint"):
            feedback.append(f"Human hint: {state['hint']}")
        last = state.get("last_report")
        contract = ImplementInput(
            task=task,
            phase=state["phase"],
            test_cmd=state["test_cmd"],
            last_report=TestReport.model_validate(last) if last else None,
            feedback=feedback,
        )
        ledger = [e["assumption"] for e in self.deps.artifacts.read_assumptions() if e.get("task_id") == task.id]
        packet = build_packet(
            "implementer",
            contract,
            budget_tokens=self._budget("implementer"),
            root=self.deps.worktree,
            files=task.files_hint,
            ledger=ledger,
        )
        log = CommandLog()
        self._update_run(current_node="implement")
        try:
            invoke_agent(
                get_spec("implementer"), packet, self._context(state, log), node="implement", task_id=task.id, call=seq
            )
            failed, problems = False, []
        except ContractViolation as exc:
            failed, problems = True, [f"implementer output rejected: {problem}" for problem in exc.problems]
        problems += [f"refused command: {cmd}" for cmd in log.refused]
        update = {"call_seq": seq, "implement_failed": failed, "last_problems": problems, "denied": list(log.denied)}
        if log.denied:
            update["escalation"] = {
                "reason": "approval",
                "task_id": task.id,
                "commands": list(log.denied),
                "options": ["approve", "deny", "abort"],
                "summary": f"{task.id} needs approval for: {', '.join(log.denied)}",
            }
        return update

    def route_after_implement(self, state: RunState) -> str:
        return "escalate" if state.get("escalation") else "verify"

    def verify(self, state: RunState) -> dict:
        task = load_plan(state).tasks[state["task_index"]]
        globs = self.deps.config.project.test_globs
        worktree = self.deps.worktree
        self._update_run(current_node="verify")
        if state.get("implement_failed"):
            return self._failed_attempt(state, state.get("last_problems", []), state.get("last_report"))
        changed = self.worktrees.changed_files(worktree, since=state["task_base_sha"])
        report = self._test(state, artifact_name("verify", task.id, state["call_seq"]))
        if state["phase"] == "red":
            problems = verify_red(changed, report, globs, state.get("base_passed"), state.get("base_skipped"))
            if not problems:
                return {
                    "phase": "green",
                    "attempts": 0,
                    "last_report": report.model_dump(),
                    "last_problems": [],
                    "red_snapshot": snapshot_tests(worktree, changed, globs),
                    "red_tree": self.worktrees.snapshot(worktree),
                    "verdict": "red_ok",
                }
        else:
            problems = verify_green(
                report,
                state.get("red_snapshot", {}),
                snapshot_tests(worktree, changed, globs),
                state.get("base_passed"),
                state.get("base_skipped"),
            )
            if not problems:
                return {"last_report": report.model_dump(), "last_problems": [], "verdict": "green_ok"}
        refused = [p for p in state.get("last_problems", []) if p.startswith("refused command: ")]
        return self._failed_attempt(state, [*problems, *refused], report.model_dump())

    def _failed_attempt(self, state: RunState, problems: list[str], report: dict | None) -> dict:
        attempts = state.get("attempts", 0) + 1
        update = {"attempts": attempts, "last_problems": problems, "last_report": report, "verdict": "retry"}
        if attempts >= self.deps.config.run.max_attempts_per_phase:
            task = load_plan(state).tasks[state["task_index"]]
            update["verdict"] = "escalate"
            update["escalation"] = {
                "reason": "attempts",
                "task_id": task.id,
                "phase": state["phase"],
                "problems": problems,
                "options": ["retry", "skip", "abort"],
                "summary": f"{task.id} failed {attempts} attempts in the {state['phase']} phase",
            }
        return update

    def escalate(self, state: RunState) -> dict:
        escalation = state["escalation"]
        self._update_run(state="escalated", current_node="escalate", needs_attention=escalation["summary"])
        payload = escalation
        while True:
            decision = interrupt(payload)
            action = decision.get("action") if isinstance(decision, dict) else None
            if action in escalation["options"]:
                break
            payload = {
                **escalation,
                "error": f"unknown escalation action {action!r}; expected one of {escalation['options']}",
            }
        self._update_run(state="running", needs_attention=None)
        cleared = {"escalation": None}
        if action == "retry":
            return {**cleared, "attempts": 0, "hint": decision.get("hint"), "next": escalation.get("resume_to", "implement")}
        if action == "bypass":
            return {**cleared, "commit_bypass": True, "next": escalation["resume_to"]}
        if action == "finish":
            note = Issue(severity="major", note="review not completed")
            return {**cleared, "open_issues": [*state.get("open_issues", []), note.model_dump()], "next": "finish"}
        if action == "skip":
            self.worktrees.reset_to(self.deps.worktree, state["task_base_sha"])
            plan = with_task_status(load_plan(state), state["task_index"], "SKIPPED")
            return {**cleared, "plan": plan.model_dump(), "next": "pick_task"}
        if action == "approve":
            # The approved call's own problems are dropped because implement re-runs the phase from its starting state.
            approved = [*state.get("approved", []), *escalation["commands"]]
            return {**cleared, "approved": approved, "denied": [], "next": "implement"}
        if action == "deny":
            hint = f"Not approved: {', '.join(escalation['commands'])}. Do not use them."
            return {**cleared, "denied": [], "hint": hint, "next": "verify"}
        if action == "continue":
            tokens, cost = run_totals(self.deps.conn, self.deps.run_id)
            limits = self.deps.config.run
            return {
                **cleared,
                "budget_limit_tokens": tokens + limits.max_tokens,
                "budget_limit_cost": cost + limits.max_cost_usd,
                "next": escalation["resume_to"],
            }
        return {**cleared, "status": "aborted", "next": "finish"}

    def route_after_escalate(self, state: RunState) -> str:
        return state["next"]

    def commit(self, state: RunState) -> dict:
        plan = load_plan(state)
        index = state["task_index"]
        task = plan.tasks[index]
        worktree = self.deps.worktree
        if self.worktrees.changed_files(worktree, since=self.worktrees.head(worktree)):
            try:
                self._commit(f"{task.id}: {task.description}", bypass=state.get("commit_bypass", False))
            except GitError as exc:
                config = self.deps.config.git
                detail = self._first_stderr_line(exc)
                return {
                    "escalation": {
                        "reason": "commit_failed",
                        "task_id": task.id,
                        "options": ["retry", "bypass", "abort"],
                        "resume_to": "commit",
                        "problems": [exc.stderr.strip() or str(exc)],
                        "summary": (
                            f"commit for {task.id} failed: {detail} "
                            f"(sign_commits={config.sign_commits}, run_hooks={config.run_hooks})"
                        ),
                    }
                }
        plan = with_task_status(plan, index, "DONE")
        done = sum(1 for item in plan.tasks if item.status == "DONE")
        self._update_run(current_node="commit", tasks_done=done, tasks_total=len(plan.tasks))
        passed = TestReport.model_validate(state["last_report"])
        return {
            "plan": plan.model_dump(),
            "base_passed": passed.passed_count,
            "base_skipped": passed.skipped_count,
            "commit_bypass": False,
        }

    def _run_tester(self, state: RunState, diff_base: str, node: str) -> dict:
        plan = load_plan(state)
        worktree = self.deps.worktree
        globs = self.deps.config.project.test_globs
        seq = state.get("call_seq", 0) + 1
        self._update_run(current_node=node)
        self.worktrees.reset_to(worktree, self.worktrees.head(worktree))
        head_before = self.worktrees.head(worktree)
        before = self._test(state, artifact_name(node, None, seq))
        notes: list[Issue] = []
        issues: list[Issue] = []
        log = CommandLog()
        contract = TesterInput(
            plan=plan, diff=self.worktrees.diff(worktree, diff_base), final_report=before, test_cmd=state["test_cmd"]
        )
        try:
            packet = build_packet("tester", contract, budget_tokens=self._budget("tester"))
            report = invoke_agent(get_spec("tester"), packet, self._context(state, log), node=node, call=seq)
            issues = list(report.issues)
        except PacketTooLarge as exc:
            notes.append(Issue(severity="major", note=f"tester skipped: {exc}"))
        except ContractViolation as exc:
            notes.append(Issue(severity="major", note=f"tester output rejected: {'; '.join(exc.problems)}"))
        product = [p for p in self.worktrees.changed_files(worktree, since=head_before) if not is_test_path(p, globs)]
        if product:
            self.worktrees.restore(worktree, product)
            notes.append(Issue(severity="minor", note=f"tester changed product files; reverted: {', '.join(product)}"))
        if self.worktrees.changed_files(worktree, since=head_before):
            try:
                self._commit(f"{plan.keyword}: tests from tester", bypass=False)
            except GitError as exc:
                self.worktrees.reset_to(worktree, head_before)
                notes.append(Issue(severity="major", note=f"tester tests not committed: {self._first_stderr_line(exc)}"))
        after = self._test(state, artifact_name(f"{node}-after", None, seq))
        notes += [Issue(severity="minor", note=f"tester command not approved: {cmd}") for cmd in log.denied]
        blocking = [issue for issue in issues if issue.severity in ("blocker", "major")]
        minor = [issue for issue in issues if issue.severity == "minor"]
        plan = issues_to_tasks(plan, blocking, "tester") if blocking else plan
        open_issues = [*state.get("open_issues", []), *(issue.model_dump() for issue in [*minor, *notes])]
        return {
            "plan": plan.model_dump(),
            "call_seq": seq,
            "open_issues": open_issues,
            "baseline_failures": sorted(set(state.get("baseline_failures", [])) | set(after.failures)),
            "base_passed": after.passed_count,
            "base_skipped": after.skipped_count,
        }

    def tester(self, state: RunState) -> dict:
        if (escalation := self._budget_escalation(state, "tester")) is not None:
            return {"escalation": escalation}
        return {**self._run_tester(state, state["base_sha"], "tester"), "tester_done": True}

    def tester_task(self, state: RunState) -> dict:
        if (escalation := self._budget_escalation(state, "tester_task")) is not None:
            return {"escalation": escalation}
        return self._run_tester(state, state["task_base_sha"], "tester_task")

    def route_after_tester(self, state: RunState) -> str:
        return "escalate" if state.get("escalation") else "pick_task"

    def route_after_commit(self, state: RunState) -> str:
        if state.get("escalation"):
            return "escalate"
        task = load_plan(state).tasks[state["task_index"]]
        audit = self.deps.config.run.tester_mode == "task+run" and task.id in state.get("original_task_ids", [])
        return "tester_task" if audit else "pick_task"

    def review(self, state: RunState) -> dict:
        if (escalation := self._budget_escalation(state, "review")) is not None:
            return {"escalation": escalation}
        plan = load_plan(state)
        worktree = self.deps.worktree
        seq = state.get("call_seq", 0) + 1
        rounds = state.get("review_rounds", 0) + 1
        self._update_run(current_node="review")
        final = self._test(state, artifact_name("review", None, seq))
        assumptions = [e["assumption"] for e in self.deps.artifacts.read_assumptions() if e.get("status") == "open"]
        contract = ReviewInput(
            plan=plan, diff=self.worktrees.diff(worktree, state["base_sha"]), final_report=final,
            open_assumptions=assumptions,
        )
        carried = list(state.get("open_issues", []))
        try:
            packet = build_packet("reviewer", contract, budget_tokens=self._budget("reviewer"))
            verdict = invoke_agent(get_spec("reviewer"), packet, self._context(state, CommandLog()), node="review", call=seq)
        except (PacketTooLarge, ContractViolation) as exc:
            problems = exc.problems if isinstance(exc, ContractViolation) else [str(exc)]
            escalation = {
                "reason": "review_failed",
                "options": ["retry", "finish", "abort"],
                "resume_to": "review",
                "problems": problems,
                "summary": "reviewer did not return a valid review",
            }
            return {"call_seq": seq, "escalation": escalation}
        blocking = [issue for issue in verdict.issues if issue.severity in ("blocker", "major")]
        minor = [issue for issue in verdict.issues if issue.severity == "minor"]
        if verdict.verdict == "changes" and blocking and rounds < self.deps.config.run.max_review_rounds:
            plan = issues_to_tasks(plan, blocking, "review")
            return {
                "plan": plan.model_dump(), "call_seq": seq, "review_rounds": rounds,
                "open_issues": [*carried, *(issue.model_dump() for issue in minor)], "next": "pick_task",
            }
        return {
            "call_seq": seq, "review_rounds": rounds,
            "open_issues": [*carried, *(issue.model_dump() for issue in verdict.issues)], "next": "finish",
        }

    def route_after_review(self, state: RunState) -> str:
        return "escalate" if state.get("escalation") else state["next"]

    def finish(self, state: RunState) -> dict:
        plan = load_plan(state)
        status = "aborted" if state.get("status") == "aborted" else "completed"
        open_issues = list(state.get("open_issues", []))
        if status == "completed":
            name = artifact_name("finish", None, state.get("call_seq", 0))
            final = self._test({**state, "baseline_failures": state.get("initial_baseline", [])}, name)
            open_issues += [
                Issue(severity="major", note=f"still failing: {failure}").model_dump()
                for failure in final.new_failures_vs_baseline
            ]
        summary = render_summary(
            run_id=self.deps.run_id,
            plan=plan,
            status=status,
            branch=branch_for(self.deps.run_id),
            base_sha=state["base_sha"],
            head_sha=self.worktrees.head(self.deps.worktree),
            open_issues=open_issues,
        )
        self.deps.artifacts.write_text("summary.md", summary)
        self._update_run(state=status, current_node="finish", needs_attention=None)
        return {"status": status, "open_issues": open_issues}
