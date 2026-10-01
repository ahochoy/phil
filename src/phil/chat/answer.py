from dataclasses import replace
from pathlib import Path

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import ANSWER_MAX_MODEL_CALLS, get_spec
from phil.contracts.routing import Answer, AnswerInput
from phil.packets import build_packet

__all__ = ["ANSWER_MAX_MODEL_CALLS", "ask_answer"]


def ask_answer(
    ctx: AgentContext, question: str, *, root: Path, overview: str, context: str = "", call: int = 1
) -> Answer:
    """Answer `question` about the repository at `root` with the read-only answerer: no run, no
    changes, at most `ANSWER_MAX_MODEL_CALLS` model calls."""
    contract = AnswerInput(question=question, context=context, repo_overview=overview)
    packet = build_packet("answerer", contract, budget_tokens=ctx.config.budget_for("answerer").max_input_tokens)
    out = invoke_agent(get_spec("answer"), packet, replace(ctx, workdir=root), node="answer", call=call)
    assert isinstance(out, Answer)
    return out
