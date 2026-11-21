import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts import Approach, ConverseTurn, Goal, Mockup, MockupInput
from phil.packets import build_packet

_SHOW = re.compile(r"show(?:\s+(\d+)[.)]?(?:\s+(.*))?)?", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ShowRequest:
    number: int | None  # None means every approach
    note: str


def parse_show(text: str) -> ShowRequest | None:
    """`show`, `show N` or `show N <note>` at the approach stage; anything else is the user's own
    approach, so None. The number's range is the caller's to check."""
    match = _SHOW.fullmatch(text.strip())
    if match is None:
        return None
    number, note = match.groups()
    return ShowRequest(int(number) if number else None, (note or "").strip())


def draw_mockup(
    ctx: AgentContext,
    goal: Goal,
    approaches: Sequence[Approach],
    *,
    tree: Path,
    overview: str,
    note: str,
    fmt: Literal["html", "sketch"],
    call: int,
    conversation: Sequence[ConverseTurn] | None = None,
    images: Sequence[Path] = (),
    previous: str = "",
) -> Mockup:
    """One drawing per approach in `approaches`, from the read-only mockup agent, which reads `tree`
    (the base commit's snapshot) and sees the goal's attached `images`. `previous`, for a redraw with
    a note, is the approach's last drawing to change. Without a designer model it uses the
    architect's, as `propose_approaches` does."""
    spec = get_spec("mockup")
    contract = MockupInput(
        goal=goal,
        approaches=list(approaches),
        repo_overview=overview,
        note=note,
        previous=previous,
        format=fmt,
        conversation=list(conversation) if conversation else None,
    )
    budget = ctx.config.budget_for("designer").max_input_tokens
    packet = build_packet(spec.role, contract, budget_tokens=budget, images=images)
    model = ctx.config.model_for("architect") if ctx.config.missing_models(("designer",)) else None
    out = invoke_agent(spec, packet, replace(ctx, workdir=tree), node="mockup", call=call, model=model)
    assert isinstance(out, Mockup)
    return out
