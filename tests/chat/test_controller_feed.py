"""The chat follows its run's activity feed: tool lines, milestone bands and the live row."""

import pytest

from phil.agents.fake import ScriptedAgentFactory, Turn, fire_tool
from phil.chat.controller import WAKE
from phil.chat.events import ChatEvent
from phil.chat.state import LiveStep
from phil.run.worker import run_worker
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import run_chat
from tests.run.conftest import TEST_CMD, review, tester_report, write_green, write_red


@pytest.fixture
def controller_with_run(calc_repo):
    """A chat whose goal was approved and whose run it follows; what it printed so far is cleared."""
    box = {}

    def grab(controller):
        box["controller"] = controller
        return None  # EOF: the chat closes, still following its run

    run_chat(calc_repo, ["add subtract", "y", grab], {"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    controller = box["controller"]
    assert controller._run_id is not None
    controller.console.export_text()  # clear
    return controller


def test_activity_and_milestones_print_feed_lines(controller_with_run):
    controller = controller_with_run
    controller._handle(ChatEvent("milestone", {
        "kind": "task_started", "task": "CALC-001", "title": "Add multiply", "ts": "2026-10-07T10:00:00Z", "seq": 2,
    }))
    controller._handle(ChatEvent("activity", {"records": [{
        "seq": 3, "ts": "2026-10-07T10:00:05Z", "phase": "end", "task": "CALC-001", "role": "implementer",
        "tool": "run_shell", "summary": "run pytest -q", "duration_ms": 1200, "result": "→ 7 passed", "ok": True,
        "detail": "3.txt",
    }]}))
    text = controller.console.export_text()
    band = text.index("▸ CALC-001 Add multiply")
    line = text.index("run   pytest -q  → 7 passed")
    assert band < line
    assert "#3" in text[line:]


def test_live_step_sets_and_clears_the_live_row(controller_with_run):
    controller = controller_with_run
    step = {"task": "T1", "role": "implementer", "summary": "run pytest", "started": 1.0}
    controller._handle(ChatEvent("live_step", step))
    assert controller.state.view().live == LiveStep("T1", "implementer", "run pytest", 1.0)
    controller._handle(ChatEvent("live_step", {}))
    assert controller.state.view().live is None

    controller._handle(ChatEvent("live_step", step))
    assert controller.state.view().live is not None
    controller._handle(ChatEvent("run_done", {"state": "failed", "needs_attention": "boom"}))
    assert controller.state.view().live is None


def _green_with_tests(turn: Turn):
    fire_tool(turn, "run_shell", {"command": "pytest -q"}, "exit_code: 0\n7 passed")
    return write_green(turn)


def test_a_scripted_run_shows_its_feed_in_the_chat(calc_repo):
    def run_it(controller):
        factory = ScriptedAgentFactory(
            {"implementer": [write_red, _green_with_tests], "tester": [tester_report()], "reviewer": [review()]}
        )
        run_worker(calc_repo, controller._run_id, "start", factory=factory, sleep=lambda _: None)
        controller._watcher.poll_once()
        return WAKE

    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", "y", run_it],
        {"intake": [goal()], "architect": [plan(test_cmd=TEST_CMD)], "critic": [critique()]},
    )
    assert runs[0].state == "completed"
    assert "▸ CALC-001" in text
    assert "run   pytest -q  → 7 passed" in text
    assert "✓ CALC-001 done" in text
