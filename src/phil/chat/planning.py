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

    def _architect(self, goal: Goal, previous: Plan | None, critique: PlanCritique | None, tree: Path) -> Plan:
        self._calls += 1
        contract = ArchitectInput(goal=goal, repo_overview=self.overview, previous_plan=previous, critique=critique)
        packet = build_packet("architect", contract, budget_tokens=_budget(self.ctx, "architect"))
        ctx = replace(self.ctx, workdir=tree)
        return invoke_agent(get_spec("architect"), packet, ctx, node="architect", call=self._calls)

    def _critic(self, goal: Goal, plan: Plan) -> PlanCritique:
        packet = build_packet("critic", CriticInput(goal=goal, plan=plan), budget_tokens=_budget(self.ctx, "critic"))
        return invoke_agent(get_spec("critic"), packet, self.ctx, node="critic", call=self._calls)

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
        plan = self._architect(goal, previous, critique, tree)
        if on_step:
            on_step("critic")
        review = self._critic(goal, plan)
        for _ in range(MAX_CRITIC_REVISIONS):
            if review.verdict != "revise":
                break
            if on_step:
                on_step("revise")
            plan = self._architect(goal, plan, review, tree)
            if on_step:
                on_step("critic")
            review = self._critic(goal, plan)
        self.version += 1
        return PlanDraft(_with_notes(plan, review), review, self.version)

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
