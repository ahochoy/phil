import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts import ArchitectInput, CriticInput, Goal, IntakeInput, Issue, Plan, PlanCritique, SelfCheck
from phil.packets import build_packet

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
) -> Goal:
    contract = IntakeInput(message=message, previous_goal=previous, answers=list(answers), repo_overview=overview)
    packet = build_packet("orchestrator", contract, budget_tokens=_budget(ctx, "orchestrator"))
    return invoke_agent(get_spec("intake"), packet, ctx, node="intake", call=call)


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
        self, goal: Goal, previous: Plan | None, critique: PlanCritique | None, tree: Path, call: int
    ) -> Plan:
        contract = ArchitectInput(goal=goal, repo_overview=self.overview, previous_plan=previous, critique=critique)
        packet = build_packet("architect", contract, budget_tokens=_budget(self.ctx, "architect"))
        ctx = replace(self.ctx, workdir=tree)
        return invoke_agent(get_spec("architect"), packet, ctx, node="architect", call=call)

    def _critic(self, goal: Goal, plan: Plan, call: int) -> PlanCritique:
        packet = build_packet("critic", CriticInput(goal=goal, plan=plan), budget_tokens=_budget(self.ctx, "critic"))
        return invoke_agent(get_spec("critic"), packet, self.ctx, node="critic", call=call)

    def _cycle(
        self,
        goal: Goal,
        previous: Plan | None,
        critique: PlanCritique | None,
        tree: Path,
        on_step: Callable[[str], None] | None = None,
    ) -> PlanDraft:
        if on_step:
            on_step("architect")
        call = self._next_call()
        plan = self._architect(goal, previous, critique, tree, call)
        if on_step:
            on_step("critic")
        review = self._critic(goal, plan, call)
        for _ in range(MAX_CRITIC_REVISIONS):
            if review.verdict != "revise":
                break
            if on_step:
                on_step("revise")
            call = self._next_call()
            plan = self._architect(goal, plan, review, tree, call)
            if on_step:
                on_step("critic")
            review = self._critic(goal, plan, call)
        return PlanDraft(_with_notes(plan, review), review, self._next_version())

    def draft(self, goal: Goal, tree: Path, on_step: Callable[[str], None] | None = None) -> PlanDraft:
        return self._cycle(goal, None, None, tree, on_step)

    def revise(
        self, goal: Goal, draft: PlanDraft, feedback: str, tree: Path, on_step: Callable[[str], None] | None = None
    ) -> PlanDraft:
        request = PlanCritique(
            verdict="revise",
            issues=[Issue(severity="major", note=f"User feedback: {feedback}")],
            notes=[],
            self_check=_empty_check(),
        )
        return self._cycle(goal, draft.plan, request, tree, on_step)
