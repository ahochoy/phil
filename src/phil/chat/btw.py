from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts import Brief, BtwInput, Goal, Plan, RunStatus
from phil.packets import build_packet


def ask_btw(
    ctx: AgentContext,
    question: str,
    *,
    goal: Goal | None = None,
    plan: Plan | None = None,
    run: RunStatus | None = None,
    recent_events: Sequence[str] = (),
    pending_question: str | None = None,
    tree: Path | None = None,
    call: int = 1,
) -> Brief:
    contract = BtwInput(
        question=question, goal=goal, plan=plan, run=run,
        recent_events=list(recent_events), pending_question=pending_question,
    )
    packet = build_packet("orchestrator", contract, budget_tokens=ctx.config.budget_for("orchestrator").max_input_tokens)
    btw_ctx = replace(ctx, workdir=tree) if tree is not None else ctx
    return invoke_agent(get_spec("btw"), packet, btw_ctx, node="btw", call=call)
