import time

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts.routing import RouteInput, RouteJudgement
from phil.packets import build_packet
from phil.routing.classes import CLASSES
from phil.routing.types import Judgement, RouteState


def judge_llm(ctx: AgentContext, state: RouteState, *, model: str | None = None, call: int = 1) -> Judgement:
    classes = {key: f"{info.description} e.g. {info.examples[0]} / {info.examples[1]}" for key, info in CLASSES.items()}
    contract = RouteInput(
        request=str(state["request"]), chat=list(state.get("chat", [])), repo=dict(state.get("repo", {})),
        classes=classes,
    )
    packet = build_packet("classifier", contract, budget_tokens=ctx.config.budget_for("classifier").max_input_tokens)
    started = time.monotonic()
    out = invoke_agent(get_spec("route"), packet, ctx, node="route", call=call, model=model)
    assert isinstance(out, RouteJudgement)
    return Judgement(
        task_class=out.task_class, probabilities={out.task_class: 1.0}, confidence=out.confidence,
        needs_detail=out.needs_detail, source="llm", latency_ms=int((time.monotonic() - started) * 1000), usage=None,
    )
