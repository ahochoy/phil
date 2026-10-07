"""The chat follows its run's activity feed: tool lines, milestone bands and the live row."""

import pytest

from phil.agents.fake import ScriptedAgentFactory, Turn, fire_tool
from phil.chat.controller import WAKE
from phil.chat.events import ChatEvent
from phil.chat.state import LiveStep
from phil.run.worker import run_worker
from phil.store.activity import ActivityLog
from phil.store.paths import ProjectPaths
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


STEP = {"task": "T1", "role": "implementer", "summary": "run pytest", "started": 1.0, "seq": 4}


def test_worker_lost_clears_the_live_step(controller_with_run):
    controller = controller_with_run
    controller._handle(ChatEvent("live_step", STEP))
    assert controller.state.view().live == LiveStep("T1", "implementer", "run pytest", 1.0)
    controller._handle(ChatEvent("worker_lost", {}))
    assert controller.state.view().live is None
    assert "stopped responding" in controller.console.export_text()


def test_forgetting_the_run_clears_the_live_step_and_the_feed(controller_with_run):
    controller = controller_with_run
    controller._handle(ChatEvent("milestone", {"kind": "task_started", "task": "CALC-001", "title": "x",
                                               "ts": "2026-10-07T10:00:00Z"}))
    controller._handle(ChatEvent("live_step", STEP))
    feed = controller._feed
    controller._forget_run()
    assert controller.state.view().live is None
    assert controller._feed is not feed and controller._feed._task_started == {}


@pytest.fixture
def controller_without_run(calc_repo):
    """A chat that has never followed a run."""
    box = {}

    def grab(controller):
        box["controller"] = controller
        return None

    run_chat(
        calc_repo, ["add subtract", "n", grab], {"intake": [goal()], "architect": [plan()], "critic": [critique()]}
    )
    controller = box["controller"]
    assert controller._run_id is None
    controller.console.export_text()  # clear
    return controller


def test_more_hash_prints_the_detail_in_a_panel(controller_with_run):
    controller = controller_with_run
    run_dir = ProjectPaths(controller.info.slug).run_dir(controller._run_id)
    ActivityLog(run_dir).end(
        14, task="CALC-001", role="implementer", tool="run_shell", summary="run pytest -q",
        result="→ 2 failed", ok=False, detail="2 failed\n", duration_ms=1200,
    )
    controller._more_command("#14")
    text = controller.console.export_text()
    assert "run pytest -q" in text
    assert "2 failed" in text


def test_more_hash_without_detail(controller_with_run):
    controller = controller_with_run
    controller._more_command("#3")
    assert controller.console.export_text().strip() == "#3 has no details."


def test_more_hash_without_a_run(controller_without_run):
    controller = controller_without_run
    controller._more_command("#3")
    assert controller.console.export_text().strip() == "No run to look in."


def test_more_hash_keeps_working_after_the_run_ends_and_is_forgotten(controller_with_run):
    controller = controller_with_run
    run_dir = ProjectPaths(controller.info.slug).run_dir(controller._run_id)
    ActivityLog(run_dir).end(
        5, task="CALC-001", role="implementer", tool="run_shell", summary="run pytest -q",
        result="→ 7 passed", ok=True, detail="7 passed\n", duration_ms=800,
    )
    controller._forget_run()
    assert controller._run_id is None  # the chat no longer follows it

    controller._more_command("#5")
    text = controller.console.export_text()
    assert "No run to look in." not in text
    assert "run pytest -q" in text
    assert "7 passed" in text


def test_more_plain_number_keeps_its_meaning(controller_with_run):
    controller = controller_with_run
    controller._more_command("2")
    assert "No detail 2. Use /show to list them." in controller.console.export_text()
    controller._more_command("x")
    assert "Usage: /more <n> or /more #<step>" in controller.console.export_text()


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
