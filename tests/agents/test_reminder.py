import json

import pytest
from langchain_core.messages import AIMessage

from phil.agents.factory import BUDGET_GRACE_CALLS
from phil.agents.invoke import AgentContext, ContractViolation, invoke_agent
from phil.agents.registry import get_spec
from phil.agents.reminder import MAX_REMINDERS, json_answer, reminder_text
from phil.contracts import CriticInput, Goal, Plan, PlanCritique, Task
from phil.packets import build_packet
from phil.store.artifacts import ArtifactStore
from tests.agents.conftest import critique
from tests.agents.test_model_retry import ScriptedChatModel, architect_packet, real_factory, tool_call, usage


class RecordingModel(ScriptedChatModel):
    """A ScriptedChatModel that also keeps the last human message each call received."""

    seen: list[str] = []

    def model_post_init(self, _context) -> None:
        super().model_post_init(_context)
        self.seen = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        humans = [m for m in messages if getattr(m, "type", None) == "human"]
        self.seen.append(str(humans[-1].content) if humans else "")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def text(content: str) -> AIMessage:
    return usage(AIMessage(content=content))


def _critic_packet():
    task = Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"])
    plan = Plan(keyword="CALC", description="Add subtract", tasks=[task])
    return build_packet("critic", CriticInput(goal=Goal(objective="Add subtract"), plan=plan), budget_tokens=8000)


def _ctx(config, conn, tmp_path, monkeypatch, model, workdir=None):
    return AgentContext(
        config=config, conn=conn, layer="chat", artifacts=ArtifactStore(tmp_path / "run"),
        workdir=workdir, factory=real_factory(model, monkeypatch),
    )


# --- json_answer -------------------------------------------------------------------------------

CRITIQUE = critique().model_dump(mode="json")


def test_a_bare_json_answer_validates():
    assert json_answer(json.dumps(CRITIQUE), PlanCritique) == critique()


def test_a_fenced_json_answer_validates():
    assert json_answer(f"```json\n{json.dumps(CRITIQUE)}\n```", PlanCritique) == critique()


def test_a_fence_wrapped_in_prose_validates():
    reply = f"Here it is:\n```json\n{json.dumps(CRITIQUE)}\n```\nHope that helps."
    assert json_answer(reply, PlanCritique) == critique()


@pytest.mark.parametrize(
    "reply",
    [
        "I think the plan is fine.",
        "{not json}",
        json.dumps({"verdict": "maybe"}),
        json.dumps([CRITIQUE]),
        f"```json\n{json.dumps(CRITIQUE)}\n```\n```json\n{json.dumps(CRITIQUE)}\n```",
    ],
)
def test_anything_else_is_not_an_answer(reply):
    assert json_answer(reply, PlanCritique) is None


def test_the_reminder_names_the_answer_tool():
    assert reminder_text("PlanCritique") == "Return your answer by calling the PlanCritique tool."


# --- the middleware, through invoke_agent ----------------------------------------------------


def test_a_lean_agent_takes_a_fenced_json_text_answer(config, conn, tmp_path, monkeypatch):
    model = RecordingModel(script=[text(f"```json\n{json.dumps(CRITIQUE)}\n```")])
    ctx = _ctx(config, conn, tmp_path, monkeypatch, model)
    assert invoke_agent(get_spec("critic"), _critic_packet(), ctx, node="critic") == critique()
    assert len(model.received) == 1
    assert [tuple(r) for r in conn.execute("SELECT outcome, reminders FROM telemetry")] == [("ok", 0)]


def test_a_reminder_then_a_tool_call_succeeds(config, conn, tmp_path, monkeypatch):
    model = RecordingModel(script=[text("The plan looks fine to me."), tool_call("PlanCritique", CRITIQUE, "c1")])
    ctx = _ctx(config, conn, tmp_path, monkeypatch, model)
    assert invoke_agent(get_spec("critic"), _critic_packet(), ctx, node="critic") == critique()
    assert model.seen[1] == reminder_text("PlanCritique")
    assert [tuple(r) for r in conn.execute("SELECT outcome, reminders FROM telemetry")] == [("ok", 1)]


def test_two_reminders_then_text_ends_the_attempt(config, conn, tmp_path, monkeypatch):
    model = RecordingModel(script=[text(f"Prose {i}.") for i in range(1, 7)])
    ctx = _ctx(config, conn, tmp_path, monkeypatch, model)
    with pytest.raises(ContractViolation) as excinfo:
        invoke_agent(get_spec("critic"), _critic_packet(), ctx, node="critic")
    assert excinfo.value.problems == ["no structured output was returned"]
    assert len(model.received) == 2 * (1 + MAX_REMINDERS)  # two attempts, each: answer + 2 reminders
    rows = [tuple(r) for r in conn.execute("SELECT outcome, reminders FROM telemetry ORDER BY id")]
    assert rows == [("invalid", MAX_REMINDERS), ("invalid", MAX_REMINDERS)]
    rejected = json.loads((tmp_path / "run" / "outputs" / "critic-run-1.rejected.json").read_text())
    assert rejected["raw"] == "Prose 3."


def test_a_deep_agent_narrating_in_text_is_reminded_and_keeps_working(config, conn, tmp_path, monkeypatch):
    from tests.agents.test_model_retry import PLAN_ARGS

    workdir = tmp_path / "repo"
    workdir.mkdir()
    (workdir / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    model = RecordingModel(
        script=[text("I'll now read calc.py."), tool_call("ls", {"path": "/"}, "c1"), tool_call("Plan", PLAN_ARGS, "c2")]
    )
    ctx = _ctx(config, conn, tmp_path, monkeypatch, model, workdir=workdir)
    plan = invoke_agent(get_spec("architect"), architect_packet(), ctx, node="architect")
    assert plan.keyword == PLAN_ARGS["keyword"]
    assert model.seen[1] == reminder_text("Plan")


def test_reminders_never_take_a_light_agent_past_its_hard_stop(config, conn, tmp_path, monkeypatch):
    spec = get_spec("answer")
    hard_stop = spec.max_model_calls + BUDGET_GRACE_CALLS
    model = RecordingModel(
        script=[tool_call("ls", {"path": "/"}, f"c{i}") for i in range(spec.max_model_calls - 1)]
        + [text("Still looking.") for _ in range(10)]
    )
    workdir = tmp_path / "repo"
    workdir.mkdir()
    ctx = _ctx(config, conn, tmp_path, monkeypatch, model, workdir=workdir)
    from phil.contracts.routing import AnswerInput

    packet = build_packet("answer", AnswerInput(question="What does calc do?"), budget_tokens=4000)
    with pytest.raises(ContractViolation):
        invoke_agent(spec, packet, ctx, node="answer", max_attempts=1)
    assert len(model.received) <= hard_stop
