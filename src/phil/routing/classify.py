"""Pick the routing backend and fall back (spec §3.4): Jev when the classifier is a systemone
model, else the classifier/low model; a Jev failure falls back to the low model; an LLM failure
(or no low model to fall back to) leaves no judgement (intake decides)."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace

from phil.agents.invoke import AgentContext
from phil.agents.providers import provider_for_model, split_model
from phil.routing.jev import JevError, judge_jev
from phil.routing.llm import judge_llm
from phil.routing.types import Judgement, RouteState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Classification:
    judgement: Judgement | None  # None: no backend judged the message, so intake decides
    fallback_reason: str | None = None  # why Jev failed, whether or not the low model then judged


def classify(
    ctx: AgentContext,
    state: RouteState,
    *,
    call: int = 1,
    transport: object | None = None,
    environ: Mapping[str, str] | None = None,
) -> Classification:
    config = ctx.config
    fallback_reason = None
    llm_model = None
    if config.is_systemone("classifier"):
        model = config.model_for("classifier")
        spec = provider_for_model(config, model, "classifier")
        try:
            judgement = judge_jev(spec, split_model(model)[1], state, timeout_s=config.routing.jev_timeout_s,
                                  environ=environ, transport=transport)
            return Classification(judgement)
        except JevError as exc:
            fallback_reason = exc.reason
            llm_model = config.tier_model("low")
            if llm_model is None:
                return Classification(None, fallback_reason)
    try:
        judgement = judge_llm(ctx, state, model=llm_model, call=call)
    except Exception as exc:  # any model, contract or config failure: intake decides
        logger.info("routing llm backend failed: %s", type(exc).__name__)
        return Classification(None, fallback_reason)
    if fallback_reason:
        judgement = replace(judgement, fallback_reason=fallback_reason)
    return Classification(judgement, fallback_reason)
