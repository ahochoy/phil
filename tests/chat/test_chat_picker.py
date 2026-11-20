"""/chat reopens another open chat; /status and /copy (full-screen spec: the in-chat commands)."""

import json

import pytest

from phil.chat import controller as controller_mod
from phil.chat.controller import HELP_COMMANDS, WAKE, ChatController, ChatOutcome
from phil.chat.events import ChatEvent
from phil.chat.state import RunView
from phil.contracts.routing import Answer
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import FULL_SCRIPT, run_chat, session_dir
from tests.chat.test_routing_flow import route


@pytest.fixture
def outcomes(monkeypatch):
    """Every `ChatController.run()` return value, in order."""
    seen = []
    real = ChatController.run

    def run(self):
        outcome = real(self)
        seen.append(outcome)
        return outcome

    monkeypatch.setattr(ChatController, "run", run)
    return seen


@pytest.fixture
def open_chat(calc_repo):
    """A saved chat waiting on a plan's approval; its id."""
    run_chat(calc_repo, ["add subtract"], FULL_SCRIPT)  # EOF at approval
    chat_id = session_dir(calc_repo).name
    state = json.loads((session_dir(calc_repo) / "state.json").read_text())
    assert state["stage"] == "approval" and state["plan"]
    return chat_id


def flat(text):
    return " ".join(text.split())


def test_chat_command_picks_another_chat(calc_repo, open_chat, outcomes):
    calls = []
    run_chat(calc_repo, ["/chat", open_chat], {}, choose=True, calls=calls)
    assert outcomes[-1] == ChatOutcome(switch_to=open_chat)
    [decision] = [c[2] for c in calls if c[0] == "choose"]
    assert decision.title == "Reopen which chat?"
    assert [o.answer for o in decision.options] == [open_chat, "stay"]
    assert decision.options[0].label.startswith("plan to approve") and "Add subtract" in decision.options[0].label
    assert decision.options[-1].label == "Stay in this chat"


def test_a_chat_that_was_switched_from_saves_its_own_stage(calc_repo, open_chat):
    run_chat(calc_repo, ["add subtract", "/chat", open_chat], FULL_SCRIPT, choose=True)
    states = [json.loads((d / "state.json").read_text()) for d in (session_dir_all(calc_repo))]
    assert sorted(s["stage"] for s in states) == ["approval", "approval"]  # never "pick_chat"


def session_dir_all(repo):
    from phil.repo import resolve_repo
    from phil.store.paths import ProjectPaths

    return sorted((ProjectPaths(resolve_repo(repo).slug).project_dir / "chats").iterdir())


def test_chat_command_stay_returns_to_the_stage(calc_repo, open_chat, outcomes):
    calls = []
    run_chat(calc_repo, ["/chat", "stay", "/quit"], {}, choose=True, calls=calls)
    assert outcomes[-1] == ChatOutcome()
    asks = [c[1] for c in calls if c[0] == "ask"]
    assert asks[-1] == asks[0] == "you › "  # back at the idle prompt


def test_typing_a_number_after_esc_switches(calc_repo, open_chat, outcomes):
    from phil.chat.controller import TYPE

    run_chat(calc_repo, ["/chat", TYPE, "1"], {}, choose=True)
    assert outcomes[-1] == ChatOutcome(switch_to=open_chat)


def test_an_empty_line_after_esc_stays(calc_repo, open_chat, outcomes):
    from phil.chat.controller import TYPE

    text, *_ = run_chat(calc_repo, ["/chat", TYPE, "", "/quit"], {}, choose=True)
    assert outcomes[-1] == ChatOutcome()
    assert "Unknown" not in text and "No such" not in text


def test_chat_command_with_none_open(calc_repo):
    text, *_ = run_chat(calc_repo, ["/chat", "/quit"], {})
    assert "No other open chats." in text


def test_pick_chat_start_with_none_open_says_so(calc_repo, outcomes):
    text, *_, prompts = run_chat(calc_repo, [], {}, pick_chat=True)
    assert "No open chats; starting a new one." in text
    assert prompts == ["you › "] and outcomes[-1] == ChatOutcome()


def test_pick_chat_start_offers_the_open_chats(calc_repo, open_chat, outcomes):
    run_chat(calc_repo, [open_chat], {}, pick_chat=True, choose=True)
    assert outcomes[-1] == ChatOutcome(switch_to=open_chat)


def test_copy_uses_io_copy(calc_repo):
    copied = []
    run_chat(
        calc_repo, ["what does calc do?", "/copy"],
        {"route": [route("question")], "answer": [Answer(text="It sums.")]},
        copy=lambda text: copied.append(text) or True,
    )
    assert copied == ["It sums."]


def test_copy_says_it_copied(calc_repo):
    text, *_ = run_chat(
        calc_repo, ["what does calc do?", "/copy"],
        {"route": [route("question")], "answer": [Answer(text="It sums.")]}, copy=lambda text: True,
    )
    assert "Copied Phil's last reply." in text


def test_copy_sent_only_to_the_terminal_says_so(calc_repo):
    from phil.chat.controller import SENT_TO_TERMINAL

    text, *_ = run_chat(
        calc_repo, ["what does calc do?", "/copy"],
        {"route": [route("question")], "answer": [Answer(text="It sums.")]}, copy=lambda text: SENT_TO_TERMINAL,
    )
    assert "Sent Phil's last reply to the terminal's clipboard." in flat(text)
    assert "Copied Phil's last reply." not in text


def test_copy_without_io_copy_says_so(calc_repo):
    text, *_ = run_chat(
        calc_repo, ["what does calc do?", "/copy"],
        {"route": [route("question")], "answer": [Answer(text="It sums.")]},
    )
    assert "Copy isn't available here; select the text instead." in flat(text)


def test_copy_with_no_reply_yet(calc_repo):
    text, *_ = run_chat(calc_repo, ["/copy"], {}, copy=lambda text: True)
    assert "Nothing to copy yet." in text


def test_status_lists_model_run_and_cost(calc_repo):
    seen = {}

    def status(controller):
        controller.state.set_run(RunView("r-0001", "CALC", None, 1, 3, 0.0, name="calc-run"))
        controller.state.set_run_usage(None, (0.42, "reported"))
        controller.state.set_cost(1.5, "reported")
        controller.state.set_parked(2)
        controller.state.set_model(("high", "sonnet"))
        view = controller.state.view()
        seen["place"] = f"{view.repo} @ {view.branch}"
        assert view.repo and view.branch
        return "/status"

    text, *_ = run_chat(calc_repo, [status], {})
    text = flat(text)
    assert seen["place"] in text
    assert "model: high·sonnet" in text
    assert "calc-run · task 2 of 3" in text and "$0.42" in text
    assert "chat $1.50" in text and "2 parked" in text


def test_status_with_no_run(calc_repo):
    text, *_ = run_chat(calc_repo, ["/status"], {})
    assert "no run" in text


def test_help_lists_chat_status_copy():
    names = [command for command, _ in HELP_COMMANDS]
    assert names.index("/chat") < names.index("/status") < names.index("/copy") < names.index("/help")
    assert dict(HELP_COMMANDS)["/chat"] == "reopen another open chat"
    assert controller_mod.help_view() is not None


def other_chat_state(repo, open_chat):
    [directory] = [d for d in session_dir_all(repo) if d.name != open_chat]
    return json.loads((directory / "state.json").read_text())


def test_eof_in_a_picker_opened_from_edit_saves_approval(calc_repo, open_chat):
    run_chat(calc_repo, ["add subtract", "edit", "/chat", None], FULL_SCRIPT, choose=True)  # EOF in the picker
    assert other_chat_state(calc_repo, open_chat)["stage"] == "approval"


def test_opening_the_picker_from_edit_saves_approval_at_once(calc_repo, open_chat):
    seen = {}

    def look(controller):
        seen["stage"] = json.loads((controller.session.dir / "state.json").read_text())["stage"]
        return None

    run_chat(calc_repo, ["add subtract", "edit", "/chat", look], FULL_SCRIPT, choose=True)
    assert seen["stage"] == "approval"


def test_a_pause_landing_in_the_picker_shows_the_pause_after_stay(calc_repo, open_chat):
    calls = []
    paused = {"summary": "CALC-001 failed 3 attempts", "options": ["retry", "skip", "abort"]}

    def pause(controller):
        controller.post(ChatEvent("run_paused", {"escalation": paused}))
        return WAKE

    run_chat(
        calc_repo, ["add subtract", "y", "/chat", pause, "stay", None], FULL_SCRIPT, choose=True, calls=calls,
    )
    chosen = [c[2] for c in calls if c[0] == "choose"]
    assert chosen[-1].kind == "pause"  # after staying, the pause menu is what's asked


def test_ctrl_c_closes_the_picker(calc_repo, open_chat):
    seen = {}

    def interrupt(controller):
        raise KeyboardInterrupt

    def look(controller):
        seen["stage"] = controller.stage
        return None

    text, *_ = run_chat(calc_repo, ["/chat", interrupt, look], {}, choose=False)
    assert "Cancelled." not in text and seen["stage"] == "idle"
