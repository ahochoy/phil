import json

from tests.chat.conftest import critique, plan
from tests.chat.test_controller import deferred, run_chat, session_dir
from tests.chat.test_conversation_flow import (  # noqa: F401
    CONVERSATION, _detectable, asks, full_goal, payloads, quick_goal, ready,
)
from tests.chat.test_reopen_state import reopen


def test_a_reopened_chat_resumes_the_conversation(calc_repo):
    run_chat(calc_repo, ["add subtract"], {"converse": [asks("Which module?")]}, config=CONVERSATION)  # EOF
    text, spawned, _, factory, _ = reopen(
        calc_repo, ["calc.py"], {"converse": [ready(quick_goal())]}, config=CONVERSATION
    )
    assert "Which module?" in text  # Phil's last reply is shown again
    resumed = payloads(factory, "converse")[0]
    assert "add subtract" in resumed and "Which module?" in resumed and '"text": "calc.py"' in resumed
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
    assert '"must_decide": false' in calls[0]
    assert len(spawned) == 1


def test_a_turn_resent_after_the_last_reply_must_decide(calc_repo):
    run_chat(calc_repo, ["add subtract"], {"converse": [asks("Which module?")]}, config=CONVERSATION)
    path = session_dir(calc_repo) / "state.json"
    state = json.loads(path.read_text())
    # Closed while the turn after the 4th reply was in flight, saved before the flag existed.
    state["stage"], state["user_replies"] = "conversation", 4
    state["conversation"] = [
        {"speaker": speaker, "text": f"{speaker} {n}", "choices": []}
        for n in range(4) for speaker in ("user", "phil")
    ] + [{"speaker": "user", "text": "user 4", "choices": []}]
    state.pop("must_decide", None)
    path.write_text(json.dumps(state))
    _, spawned, _, factory, _ = reopen(calc_repo, [], {"converse": [ready(quick_goal())]}, config=CONVERSATION)
    [call] = payloads(factory, "converse")
    assert '"must_decide": true' in call
    assert len(spawned) == 1


def test_a_go_turn_in_flight_is_resent_on_reopen(calc_repo):
    submit, run_next, pending = deferred()
    run_chat(  # EOF while the `go` turn is pending
        calc_repo, ["add subtract", run_next, run_next, "go"], {"converse": [asks("Which module?")]},  # route, turn
        config=CONVERSATION, submit=submit,
    )
    state = json.loads((session_dir(calc_repo) / "state.json").read_text())
    assert state["stage"] == "conversation" and state["must_decide"] is True
    _, spawned, _, factory, _ = reopen(calc_repo, [], {"converse": [ready(quick_goal())]}, config=CONVERSATION)
    [call] = payloads(factory, "converse")
    assert '"must_decide": true' in call and "Which module?" in call
    assert len(spawned) == 1


def test_an_edit_after_reopening_at_approval_keeps_the_conversation(calc_repo):
    run_chat(  # EOF at approval
        calc_repo, ["build a calculator CLI", "argparse"],
        {"converse": [asks("Which CLI library?"), ready(full_goal())], "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    _, _, _, factory, _ = reopen(
        calc_repo, ["edit", "make it two tasks", "n"], {"architect": [plan(n=2)], "critic": [critique()]},
        config=CONVERSATION,
    )
    architect = payloads(factory, "architect")[0]
    assert "Which CLI library?" in architect and "argparse" in architect
