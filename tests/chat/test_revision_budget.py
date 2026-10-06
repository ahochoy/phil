"""A revision runs the architect on a smaller call budget: the first pass already read the repo,
and its plan records what it found (a live revision cost as much as a whole first pass)."""

from phil.agents.fake import ScriptedAgentFactory
from phil.agents.registry import ARCHITECT_MAX_MODEL_CALLS, ARCHITECT_REVISE_MAX_MODEL_CALLS, get_spec
from phil.agents.spec import load_prompt
from phil.chat.planning import Planner
from tests.chat.conftest import critique, goal, issue, plan


class CapRecordingFactory(ScriptedAgentFactory):
    """Records each architect build's call cap."""

    def __init__(self, scripts) -> None:
        super().__init__(scripts)
        self.caps: list[int | None] = []

    def __call__(self, spec, *args, **kw):
        if spec.name == "architect":
            self.caps.append(spec.max_model_calls)
        return super().__call__(spec, *args, **kw)


def test_the_revision_budget_is_smaller_than_a_first_pass():
    assert ARCHITECT_REVISE_MAX_MODEL_CALLS == 4 < ARCHITECT_MAX_MODEL_CALLS


def test_a_critic_revision_runs_on_the_revision_budget(chat_ctx, tmp_path):
    factory = CapRecordingFactory({
        "architect": [plan(), plan(n=2)],
        "critic": [critique("revise", [issue("too big", "CALC-001")]), critique()],
    })
    Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path)
    assert factory.caps == [ARCHITECT_MAX_MODEL_CALLS, ARCHITECT_REVISE_MAX_MODEL_CALLS]


def test_a_user_edit_runs_on_the_revision_budget(chat_ctx, tmp_path):
    factory = CapRecordingFactory({"architect": [plan(), plan(n=3)], "critic": [critique(), critique()]})
    planner = Planner(chat_ctx(factory), "overview")
    first = planner.draft(goal(), tmp_path)
    planner.revise(goal(), first, "split task 1 in three", tmp_path)
    assert factory.caps == [ARCHITECT_MAX_MODEL_CALLS, ARCHITECT_REVISE_MAX_MODEL_CALLS]


def test_the_prompt_states_the_revision_budget():
    prompt = load_prompt(get_spec("architect"))
    revisions = prompt[prompt.index("## Revisions"):]
    assert f"about {ARCHITECT_REVISE_MAX_MODEL_CALLS} model calls" in revisions
