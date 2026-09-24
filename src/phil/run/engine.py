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
from phil.contracts import ImplementInput, Issue, TesterInput, TestReport
from phil.git import branch_for
from phil.packets import PacketTooLarge, build_packet
from phil.run.gates import is_test_path, run_tests, snapshot_tests, verify_green, verify_red
from phil.run.state import RunState, issues_to_tasks, load_plan, next_todo, render_summary, with_task_status
from phil.store.artifacts import ArtifactStore, artifact_name
from phil.store.runs import update_run
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
        graph.add_edge(START, "setup")
        graph.add_edge("setup", "pick_task")
        graph.add_conditional_edges("pick_task", self.route_after_pick, ["implement", "tester", "finish"])
        graph.add_node("escalate", self.escalate)
        graph.add_conditional_edges("implement", self.route_after_implement, ["verify", "escalate"])
        graph.add_conditional_edges("verify", self.route_after_verify, ["implement", "commit", "escalate"])
        graph.add_conditional_edges(
            "escalate", self.route_after_escalate, ["implement", "verify", "pick_task", "finish"]
        )
        graph.add_conditional_edges("commit", self.route_after_commit, ["tester_task", "pick_task"])
        graph.add_edge("tester", "pick_task")
        graph.add_edge("tester_task", "pick_task")
        graph.add_edge("finish", END)
        return graph.compile(checkpointer=checkpointer)

    def route_after_pick(self, state: RunState) -> str:
        if state["task_index"] >= 0:
            return "implement"
        return "finish" if state.get("tester_done") else "tester"

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

    # --- nodes -------------------------------------------------------------

    def setup(self, state: RunState) -> dict:
        plan = load_plan(state)
        if not self.deps.worktree.exists():
            self.worktrees.create(run_id=self.deps.run_id, base_sha=state["base_sha"], path=self.deps.worktree)
        self.deps.artifacts.write_plan(plan)
        baseline = self._test({**state, "baseline_failures": []}, "baseline")
        self._update_run(state="running", current_node="setup", tasks_total=len(plan.tasks))
        return {"baseline_failures": baseline.failures, "status": "running"}

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
            problems = verify_red(changed, report, globs)
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
            problems = verify_green(report, state.get("red_snapshot", {}), snapshot_tests(worktree, changed, globs))
            if not problems:
                return {"last_report": report.model_dump(), "last_problems": [], "verdict": "green_ok"}
        return self._failed_attempt(state, problems, report.model_dump())

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
            return {**cleared, "attempts": 0, "hint": decision.get("hint"), "next": "implement"}
        if action == "skip":
            self.worktrees.reset_to(self.deps.worktree, state["task_base_sha"])
            plan = with_task_status(load_plan(state), state["task_index"], "SKIPPED")
            return {**cleared, "plan": plan.model_dump(), "next": "pick_task"}
        if action == "approve":
            approved = [*state.get("approved", []), *escalation["commands"]]
            return {**cleared, "approved": approved, "denied": [], "next": "implement"}
        if action == "deny":
            hint = f"Not approved: {', '.join(escalation['commands'])}. Do not use them."
            return {**cleared, "denied": [], "hint": hint, "next": "verify"}
        return {**cleared, "status": "aborted", "next": "finish"}

    def route_after_escalate(self, state: RunState) -> str:
        return state["next"]

    def commit(self, state: RunState) -> dict:
        plan = load_plan(state)
        index = state["task_index"]
        task = plan.tasks[index]
        worktree = self.deps.worktree
        if self.worktrees.changed_files(worktree, since=self.worktrees.head(worktree)):
            self.worktrees.commit_all(worktree, f"{task.id}: {task.description}")
        plan = with_task_status(plan, index, "DONE")
        done = sum(1 for item in plan.tasks if item.status == "DONE")
        self._update_run(current_node="commit", tasks_done=done, tasks_total=len(plan.tasks))
        return {"plan": plan.model_dump()}

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
            notes.append(Issue(severity="minor", note=f"tester skipped: {exc}"))
        except ContractViolation as exc:
            notes.append(Issue(severity="minor", note=f"tester output rejected: {'; '.join(exc.problems)}"))
        product = [p for p in self.worktrees.changed_files(worktree, since=head_before) if not is_test_path(p, globs)]
        if product:
            self.worktrees.restore(worktree, product)
            notes.append(Issue(severity="minor", note=f"tester changed product files; reverted: {', '.join(product)}"))
        if self.worktrees.changed_files(worktree, since=head_before):
            self.worktrees.commit_all(worktree, f"{plan.keyword}: tests from tester")
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
        }

    def tester(self, state: RunState) -> dict:
        return {**self._run_tester(state, state["base_sha"], "tester"), "tester_done": True}

    def tester_task(self, state: RunState) -> dict:
        return self._run_tester(state, state["task_base_sha"], "tester_task")

    def route_after_commit(self, state: RunState) -> str:
        task = load_plan(state).tasks[state["task_index"]]
        audit = self.deps.config.run.tester_mode == "task+run" and task.id in state.get("original_task_ids", [])
        return "tester_task" if audit else "pick_task"

    def finish(self, state: RunState) -> dict:
        plan = load_plan(state)
        status = "aborted" if state.get("status") == "aborted" else "completed"
        summary = render_summary(
            run_id=self.deps.run_id,
            plan=plan,
            status=status,
            branch=branch_for(self.deps.run_id),
            base_sha=state["base_sha"],
            head_sha=self.worktrees.head(self.deps.worktree),
            open_issues=state.get("open_issues", []),
        )
        self.deps.artifacts.write_text("summary.md", summary)
        self._update_run(state=status, current_node="finish", needs_attention=None)
        return {"status": status}
