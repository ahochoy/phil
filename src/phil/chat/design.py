from dataclasses import replace
from pathlib import Path

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts import Approaches, DesignInput, Goal
from phil.packets import build_packet


def propose_approaches(ctx: AgentContext, goal: Goal, *, tree: Path, overview: str, call: int = 1) -> Approaches:
    """2–3 approaches to `goal` from the read-only designer, which reads `tree` (the base commit's
    snapshot, as the architect does). Without a designer model it uses the architect's."""
    contract = DesignInput(goal=goal, repo_overview=overview)
    packet = build_packet("designer", contract, budget_tokens=ctx.config.budget_for("designer").max_input_tokens)
    # A legacy per-role config has no high tier: the designer then designs on the architect's model.
    model = ctx.config.model_for("architect") if ctx.config.missing_models(("designer",)) else None
    out = invoke_agent(get_spec("design"), packet, replace(ctx, workdir=tree), node="design", call=call, model=model)
    assert isinstance(out, Approaches)
    return out
