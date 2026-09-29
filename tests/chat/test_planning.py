from phil.agents.fake import ScriptedAgentFactory
from phil.chat.planning import Planner, intake
from tests.chat.conftest import critique, goal, issue, plan


def test_intake_passes_message_answers_and_previous_goal(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({"intake": [goal(open_questions=["Which file?"]), goal()]})
    ctx = chat_ctx(factory)
    first = intake(ctx, "add subtract", overview="Tracked files:\ncalc.py")
    assert first.open_questions == ["Which file?"]
    second = intake(ctx, "add subtract", overview="", previous=first, answers=["calc.py"], call=2)
    assert second.open_questions == []
    payload = str(factory.calls[1][1])
    assert "calc.py" in payload and "Which file?" in payload


def test_draft_accepts_an_ok_critique(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({"architect": [plan()], "critic": [critique(notes=["small plan"])]})
    draft = Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path)
    assert draft.version == 1
    assert draft.plan.critic_notes == ["small plan"]
    assert factory.remaining() == {"architect": 0, "critic": 0}


def test_draft_revises_once_on_a_revise_verdict(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({
        "architect": [plan(n=1), plan(n=2)],
        "critic": [critique("revise", [issue("too big", "CALC-001")]), critique("revise", [issue("still meh")])],
    })
    draft = Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path)
    assert len(draft.plan.tasks) == 2
    assert draft.critique.verdict == "revise"
    assert draft.plan.critic_notes == ["plan: still meh"]
    architect_payloads = [p for role, p in factory.calls if role == "architect"]
    assert "too big" in str(architect_payloads[1])


def test_revise_sends_user_feedback_and_bumps_the_version(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({"architect": [plan(), plan(n=3)], "critic": [critique(), critique()]})
    planner = Planner(chat_ctx(factory), "overview")
    first = planner.draft(goal(), tmp_path)
    second = planner.revise(goal(), first, "split task 1 in three", tmp_path)
    assert second.version == 2
    assert len(second.plan.tasks) == 3
    last_architect = [p for role, p in factory.calls if role == "architect"][-1]
    assert "User feedback: split task 1 in three" in str(last_architect)


def test_architect_reads_the_given_tree(chat_ctx, tmp_path):
    seen = []

    def architect(turn):
        seen.append(turn.workdir)
        return plan()

    factory = ScriptedAgentFactory({"architect": [architect, architect], "critic": [critique(), critique()]})
    planner = Planner(chat_ctx(factory), "overview")
    first = planner.draft(goal(), tmp_path / "a")
    planner.revise(goal(), first, "more", tmp_path / "b")
    assert seen == [tmp_path / "a", tmp_path / "b"]


def test_planner_reports_steps(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({
        "architect": [plan(n=1), plan(n=2)],
        "critic": [critique("revise", [issue("too big")]), critique()],
    })
    steps = []
    Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path, on_step=steps.append)
    assert steps == ["architect", "critic", "revise", "critic"]
