import json

from tests.chat.test_controller import run_chat, session_dir
from tests.chat.test_conversation_flow import CONVERSATION, _detectable, asks, payloads, quick_goal, ready  # noqa: F401
from tests.chat.test_reopen_state import reopen


def test_a_reopened_chat_resumes_the_conversation(calc_repo):
    run_chat(calc_repo, ["add subtract"], {"converse": [asks("Which module?")]}, config=CONVERSATION)  # EOF
    text, spawned, _, factory, _ = reopen(
        calc_repo, ["calc.py"], {"converse": [ready(quick_goal())]}, config=CONVERSATION
    )
    assert "Which module?" in text  # Phil's last reply is shown again
    resumed = payloads(factory, "converse")[0]
    assert "add subtract" in resumed and "calc.py" in resumed
    assert len(spawned) == 1


def test_a_chat_closed_mid_turn_resends_the_unanswered_turn(calc_repo):
    run_chat(calc_repo, ["add subtract"], {"converse": [asks("Which module?")]}, config=CONVERSATION)
    path = session_dir(calc_repo) / "state.json"
    state = json.loads(path.read_text())
    # As if the chat closed while the first turn was in flight: the user's message is the last turn.
    state["stage"] = "conversation"
    state["conversation"] = [{"speaker": "user", "text": "add subtract", "choices": []}]
    path.write_text(json.dumps(state))
    _, spawned, _, factory, _ = reopen(calc_repo, [], {"converse": [ready(quick_goal())]}, config=CONVERSATION)
    calls = payloads(factory, "converse")
    assert len(calls) == 1 and "add subtract" in calls[0]
    assert len(spawned) == 1
