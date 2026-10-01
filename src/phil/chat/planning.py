import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts import (
    ArchitectInput,
    AttemptWorklog,
    CriticInput,
    Goal,
    IntakeInput,
    Issue,
    Plan,
    PlanCritique,
    SelfCheck,
)
from phil.packets import build_packet
from phil.repo_detect import detect_test_cmd

MAX_CRITIC_REVISIONS = 1


def _empty_check() -> SelfCheck:
    return SelfCheck(assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[])


def _budget(ctx: AgentContext, role: str) -> int:
    return ctx.config.budget_for(role).max_input_tokens


def intake(
    ctx: AgentContext,
    message: str,
    *,
    overview: str,
    previous: Goal | None = None,
    answers: Sequence[str] = (),
    call: int = 1,
    route_depth: Literal["answer", "quick", "full"] | None = None,
    detected_test_cmd: str | None = None,
) -> Goal:
    contract = IntakeInput(
        message=message,
        previous_goal=previous,
        answers=list(answers),
        repo_overview=overview,
        route_depth=route_depth,
        detected_test_cmd=detected_test_cmd,
    )
    packet = build_packet("orchestrator", contract, budget_tokens=_budget(ctx, "orchestrator"))
    return invoke_agent(get_spec("intake"), packet, ctx, node="intake", call=call)


def quick_plan(goal: Goal, test_cmd: str | None) -> Plan | None:
    """The one-task plan for a quick goal, or None when the goal has no valid quick task (spec §4.1)."""
    task = goal.task
    if task is None:
        return None
    keyword = task.id.split("-", 1)[0]
    try:
        return Plan(
            keyword=keyword, description=goal.objective, tasks=[task.model_copy(update={"status": "TODO"})],
            test_cmd=test_cmd,
        )
    except ValidationError:
        return None


def _with_notes(plan: Plan, critique: PlanCritique) -> Plan:
    notes = [*critique.notes, *(f"{i.task_id or 'plan'}: {i.note}" for i in critique.issues)]
    return plan.model_copy(update={"critic_notes": notes})


@dataclass(frozen=True)
class PlanDraft:
    plan: Plan
    critique: PlanCritique
    version: int


class Planner:
    """Architect → critic cycles. The architect reads `tree`, a read-only snapshot of the base commit."""

    def __init__(self, ctx: AgentContext, overview: str) -> None:
        self.ctx = ctx
        self.overview = overview
        self.version = 0
        self._calls = 0
        # Chat jobs run on worker threads, and a replaced goal's cycle may overlap the new one's.
        self._lock = threading.Lock()

    def _next_call(self) -> int:
        with self._lock:
            self._calls += 1
            return self._calls

    def counters(self) -> tuple[int, int]:
        """(calls, version) so far — saved with the chat so a reopened chat keeps numbering from here."""
        with self._lock:
            return self._calls, self.version

    def restore(self, calls: int, version: int) -> None:
        """Continue numbering agent calls and plan versions after a reopened chat's earlier ones."""
        with self._lock:
            self._calls, self.version = max(self._calls, calls), max(self.version, version)

    def _next_version(self) -> int:
        with self._lock:
            self.version += 1
            return self.version

    def _architect(
        self,
        ctx: AgentContext,
        goal: Goal,
        previous: Plan | None,
        critique: PlanCritique | None,
        tree: Path,
        call: int,
        prior_attempt: Sequence[AttemptWorklog] = (),
    ) -> Plan:
        contract = ArchitectInput(
            goal=goal,
            repo_overview=self.overview,
            previous_plan=previous,
            critique=critique,
            detected_test_cmd=detect_test_cmd(tree),  # the snapshot is the tracked files the run will see
            prior_attempt=list(prior_attempt),
        )
        packet = build_packet("architect", contract, budget_tokens=_budget(ctx, "architect"))
        return invoke_agent(get_spec("architect"), packet, replace(ctx, workdir=tree), node="architect", call=call)

    def _critic(self, ctx: AgentContext, goal: Goal, plan: Plan, call: int) -> PlanCritique:
        packet = build_packet("critic", CriticInput(goal=goal, plan=plan), budget_tokens=_budget(ctx, "critic"))
        return invoke_agent(get_spec("critic"), packet, ctx, node="critic", call=call)

    def _cycle(
        self,
        goal: Goal,
        previous: Plan | None,
        critique: PlanCritique | None,
        tree: Path,
        on_step: Callable[[str], None] | None = None,
        ctx: AgentContext | None = None,
        prior_attempt: Sequence[AttemptWorklog] = (),
    ) -> PlanDraft:
        ctx = self.ctx if ctx is None else ctx
        if on_step:
            on_step("architect")
        call = self._next_call()
        plan = self._architect(ctx, goal, previous, critique, tree, call, prior_attempt)
        if on_step:
            on_step("critic")
        review = self._critic(ctx, goal, plan, call)
        for _ in range(MAX_CRITIC_REVISIONS):
            if review.verdict != "revise":
                break
            if on_step:
                on_step("revise")
            call = self._next_call()
            plan = self._architect(ctx, goal, plan, review, tree, call, prior_attempt)
            if on_step:
                on_step("critic")
            review = self._critic(ctx, goal, plan, call)
        return PlanDraft(_with_notes(plan, review), review, self._next_version())

    def draft(
        self,
        goal: Goal,
        tree: Path,
        on_step: Callable[[str], None] | None = None,
        *,
        ctx: AgentContext | None = None,
        prior_attempt: Sequence[AttemptWorklog] = (),
    ) -> PlanDraft:
        """`ctx` overrides the planner's context for this cycle (a chat job passes one with its own connection)."""
        return self._cycle(goal, None, None, tree, on_step, ctx, prior_attempt)

    def revise(
        self,
        goal: Goal,
        draft: PlanDraft,
        feedback: str,
        tree: Path,
        on_step: Callable[[str], None] | None = None,
        *,
        ctx: AgentContext | None = None,
        prior_attempt: Sequence[AttemptWorklog] = (),
    ) -> PlanDraft:
        request = PlanCritique(
            verdict="revise",
            issues=[Issue(severity="major", note=f"User feedback: {feedback}")],
            notes=[],
            self_check=_empty_check(),
        )
        return self._cycle(goal, draft.plan, request, tree, on_step, ctx, prior_attempt)
