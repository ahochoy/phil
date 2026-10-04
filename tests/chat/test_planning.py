import pytest

from phil.agents.fake import ScriptedAgentFactory
from phil.chat.planning import Planner, intake, quick_plan
from phil.contracts import AttemptWorklog, Question, Task
from tests.chat.conftest import critique, goal, issue, plan


def test_intake_passes_message_answers_and_previous_goal(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({"intake": [goal(open_questions=["Which file?"]), goal()]})
    ctx = chat_ctx(factory)
    first = intake(ctx, "add subtract", overview="Tracked files:\ncalc.py")
    assert first.open_questions == [Question(text="Which file?")]
    second = intake(ctx, "add subtract", overview="", previous=first, answers=["calc.py"], call=2)
    assert second.open_questions == []
    payload = str(factory.calls[1][1])
    assert "calc.py" in payload and "Which file?" in payload


def test_intake_passes_route_depth_and_detected_test_cmd(chat_ctx):
    factory = ScriptedAgentFactory({"intake": [goal()]})
    ctx = chat_ctx(factory)
    intake(ctx, "add subtract", overview="", route_depth="quick", detected_test_cmd="uv run pytest -q")
    payload = str(factory.calls[0][1])
    assert '"route_depth": "quick"' in payload
    assert '"detected_test_cmd": "uv run pytest -q"' in payload


def _task(task_id="CALC-001") -> Task:
    return Task(id=task_id, description="Add subtract", acceptance_criteria=["subtract works"])


def test_quick_plan_builds_a_one_task_plan_whose_keyword_is_the_task_prefix():
    result = quick_plan(goal(task=_task()), "uv run pytest -q")
    assert result is not None
    assert result.keyword == "CALC"
    assert result.tasks == [_task()]
    assert result.test_cmd == "uv run pytest -q"


def test_quick_plan_returns_none_without_a_task():
    assert quick_plan(goal(), "uv run pytest -q") is None


QUICK_TASK = {"id": "CALC-001", "description": "Add subtract", "acceptance_criteria": ["subtract works"]}


@pytest.mark.parametrize(
    "fields",
    [
        {"id": "toolongkeyword-001"},
        {"id": "calc-1"},
        {"verify": "check"},  # a check task with no check_cmd
        {"check_cmd": "make lint"},  # a tdd task with a check_cmd
        {"acceptance_criteria": []},
    ],
    ids=["long-keyword", "malformed-id", "check-without-cmd", "tdd-with-cmd", "no-criteria"],
)
def test_quick_plan_returns_none_for_a_task_a_plan_rejects(fields):
    assert quick_plan(goal(task=QUICK_TASK | fields), None) is None


def test_quick_plan_turns_the_lenient_quick_task_into_a_todo_task():
    result = quick_plan(goal(task=QUICK_TASK | {"verify": "check", "check_cmd": "make lint"}), None)
    assert result is not None
    assert result.tasks == [Task(**QUICK_TASK, verify="check", check_cmd="make lint", status="TODO")]


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


def test_a_cycle_keeps_its_call_number_when_another_cycle_interleaves(chat_ctx, tmp_path):
    # Worker threads can run two goals' cycles at once; each critic call must match its own architect call.
    nested = {}

    def architect(turn):
        if not nested:
            nested["draft"] = planner.draft(goal(), tmp_path)  # another cycle runs mid-call
        return plan()

    ctx = chat_ctx(ScriptedAgentFactory({"architect": [architect, plan()], "critic": [critique(), critique()]}))
    planner = Planner(ctx, "overview")
    outer = planner.draft(goal(), tmp_path)
    rows = ctx.conn.execute("SELECT node, call FROM telemetry ORDER BY rowid").fetchall()
    assert sorted(tuple(r) for r in rows) == [("architect", 1), ("architect", 2), ("critic", 1), ("critic", 2)]
    assert {outer.version, nested["draft"].version} == {1, 2}


def test_architect_gets_the_detected_test_command_as_a_hint(chat_ctx, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'calc'\n")
    (tmp_path / "uv.lock").write_text("")
    factory = ScriptedAgentFactory({"architect": [plan()], "critic": [critique()]})
    Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path)
    [architect] = [p for role, p in factory.calls if role == "architect"]
    assert '"detected_test_cmd": "uv run pytest"' in str(architect)


def test_architect_hint_is_null_when_nothing_is_detected(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({"architect": [plan()], "critic": [critique()]})
    Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path)
    [architect] = [p for role, p in factory.calls if role == "architect"]
    assert '"detected_test_cmd": null' in str(architect)


def test_draft_sends_prior_attempt_to_the_architect(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({"architect": [plan()], "critic": [critique()]})
    worklog = AttemptWorklog(files_changed=["calc.py"], notes=["tried X, failed because Y"])
    Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path, prior_attempt=[worklog])
    [architect] = [p for role, p in factory.calls if role == "architect"]
    assert "tried X, failed because Y" in str(architect)


def test_revise_sends_prior_attempt_to_the_architect(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({"architect": [plan(), plan()], "critic": [critique(), critique()]})
    worklog = AttemptWorklog(files_changed=["calc.py"], notes=["tried X, failed because Y"])
    planner = Planner(chat_ctx(factory), "overview")
    draft = planner.draft(goal(), tmp_path, prior_attempt=[worklog])
    planner.revise(goal(), draft, "smaller tasks", tmp_path, prior_attempt=[worklog])
    revision = [p for role, p in factory.calls if role == "architect"][-1]
    assert "tried X, failed because Y" in str(revision) and "smaller tasks" in str(revision)


def test_draft_sends_prior_attempt_to_both_the_first_plan_and_a_revision(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({
        "architect": [plan(n=1), plan(n=2)],
        "critic": [critique("revise", [issue("too big", "CALC-001")]), critique()],
    })
    worklog = AttemptWorklog(files_changed=["calc.py"], notes=["tried X, failed because Y"])
    Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path, prior_attempt=[worklog])
    architect_payloads = [p for role, p in factory.calls if role == "architect"]
    assert all("tried X, failed because Y" in str(p) for p in architect_payloads)
