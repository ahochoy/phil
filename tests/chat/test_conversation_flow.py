import pytest

from phil.contracts import Goal, IntakeTurn, QuickTask
from phil.contracts.routing import RouteJudgement
from tests.chat.conftest import critique, plan
from tests.chat.test_controller import run_chat
from tests.chat.test_quick_flow import detectable
from tests.helpers import TEST_MODELS

from phil.config import PhilConfig


@pytest.fixture(autouse=True)
def _detectable(calc_repo):
    """A detectable test command (`pytest`) in the base commit, so a valid quick task makes a quick run."""
    detectable(calc_repo)

CONVERSATION = PhilConfig(models=TEST_MODELS, chat={"intake": "conversation"})
STEPS = ["Add subtract to calc.py", "Test it"]
QUICK_TASK = QuickTask(
    id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"],
    files_hint=["calc.py"], verify="tdd",
)


def quick_goal(**kw):
    return Goal(objective="Add subtract to calc", depth="quick", task=QUICK_TASK, **kw)


def full_goal(**kw):
    return Goal(objective="Add a calculator CLI", depth="full", **kw)


def ready(goal, reply="Adding subtract.", steps=STEPS):
    return IntakeTurn(reply=reply, ready=True, goal=goal, steps=steps)


def asks(reply="Which module?", choices=()):
    return IntakeTurn(reply=reply, choices=list(choices), ready=False)


def payloads(factory, role):
    return [str(payload) for name, payload in factory.calls if name == role]


def test_a_clear_request_shows_its_plan_and_starts(calc_repo):
    text, spawned, runs, factory, _ = run_chat(
        calc_repo, ["add subtract"], {"converse": [ready(quick_goal())]}, config=CONVERSATION
    )
    assert "Adding subtract." in text
    assert "Plan" in text and "Add subtract to calc.py" in text and "Test it" in text
    assert "Starting (quick)" in text
    assert [mode for _, mode, _ in spawned] == ["start"]
    assert factory.remaining() == {"converse": 0}


def test_a_question_then_an_answer_then_ready(calc_repo):
    text, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "calc.py please"],
        {"converse": [asks(), ready(quick_goal())]}, config=CONVERSATION,
    )
    assert "Which module?" in text
    second = payloads(factory, "converse")[1]
    assert "add subtract" in second and "Which module?" in second and "calc.py please" in second
    assert len(spawned) == 1


# Choice labels that appear nowhere else in a payload (not in the repo overview), so counting
# them says whether the user's turn is the choice: once in Phil's turn's choices, once more if picked.
CHOICES = ["Option Alpha", "Option Beta"]


def test_a_typed_number_picks_a_choice(calc_repo):
    _, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "2"],
        {"converse": [asks(choices=CHOICES), ready(quick_goal())]}, config=CONVERSATION,
    )
    second = payloads(factory, "converse")[1]
    assert second.count("Option Beta") == 2 and second.count("Option Alpha") == 1


def test_a_number_that_matches_no_choice_is_the_users_own_answer(calc_repo):
    _, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "7"],
        {"converse": [asks(choices=CHOICES), ready(quick_goal())]}, config=CONVERSATION,
    )
    calls = payloads(factory, "converse")
    assert len(calls) == 2  # sent as an answer, not re-prompted
    assert calls[1].count("Option Beta") == 1 and calls[1].count("Option Alpha") == 1
    assert '"text": "7"' in calls[1]
    assert len(spawned) == 1


@pytest.mark.xfail(strict=True, reason="Task 6")
def test_a_ready_full_goal_goes_to_the_architect_with_the_conversation(calc_repo):
    text, spawned, _, factory, _ = run_chat(
        calc_repo, ["build a calculator CLI", "argparse", "y"],
        {"converse": [asks("Which CLI library?"), ready(full_goal(), reply="Planning the CLI.")],
         "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    assert "Planning in detail" in text
    architect = payloads(factory, "architect")[0]
    assert "Which CLI library?" in architect and "argparse" in architect
    assert len(spawned) == 1  # approved with "y"


@pytest.mark.xfail(strict=True, reason="Task 6")
def test_an_open_approach_goes_to_the_designer_with_the_conversation(calc_repo):
    from phil.contracts import Approach, Approaches

    approaches = Approaches(options=[Approach(name="Argparse", summary="a"), Approach(name="Click", summary="b")])
    _, _, _, factory, _ = run_chat(  # EOF at the approach menu ends the chat
        calc_repo, ["build a calculator CLI"],
        {"converse": [ready(full_goal(approach_open=True))], "design": [approaches]},
        config=CONVERSATION,
    )
    assert "build a calculator CLI" in payloads(factory, "design")[0]


def test_the_conversation_may_make_a_quick_route_full(calc_repo):
    quick_route = RouteJudgement(task_class="simple_change", confidence=0.95, needs_detail=0.1)
    _, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "n"],
        {"route": [quick_route], "converse": [ready(full_goal())], "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    assert "simple_change" in payloads(factory, "converse")[0]
    assert payloads(factory, "architect") and spawned == []  # full path, plan declined with "n"


def test_the_conversation_may_make_a_full_route_quick(calc_repo):
    full_route = RouteJudgement(task_class="feature", confidence=0.95, needs_detail=0.1)
    _, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract"], {"route": [full_route], "converse": [ready(quick_goal())]}, config=CONVERSATION
    )
    assert len(spawned) == 1 and not payloads(factory, "architect")


def test_a_quick_goal_without_a_valid_task_still_shows_its_plan_then_plans_fully(calc_repo):
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "n"],
        {"converse": [ready(Goal(objective="Add subtract to calc", depth="quick"))],
         "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    assert "Add subtract to calc.py" in text  # the Plan block
    assert "Couldn't plan this as a quick change" in text
    assert payloads(factory, "architect")


def test_forced_paths_skip_the_conversation(calc_repo):
    _, spawned, _, factory, _ = run_chat(
        calc_repo, ["/quick add subtract"], {"intake": [quick_goal()]}, config=CONVERSATION
    )
    assert not payloads(factory, "converse") and len(spawned) == 1


def test_questions_still_go_to_the_answerer(calc_repo):
    from phil.contracts.routing import Answer

    question = RouteJudgement(task_class="question", confidence=0.95, needs_detail=0.1)
    text, _, _, factory, _ = run_chat(
        calc_repo, ["what does calc do?"],
        {"route": [question], "answer": [Answer(text="It adds.")]}, config=CONVERSATION,
    )
    assert "It adds." in text and not payloads(factory, "converse")


def test_the_menu_offers_the_choices(calc_repo):
    calls = []
    run_chat(
        calc_repo, ["add subtract", "2"],
        {"converse": [asks(choices=["app.py", "calc.py"]), ready(quick_goal())]},
        config=CONVERSATION, choose=True, calls=calls,
    )
    menus = [call[2] for call in calls if call[0] == "choose"]
    labels = [option.label for option in menus[0].options]
    assert labels == ["app.py", "calc.py", "Something else (type it)", "Plan with what you know"]
