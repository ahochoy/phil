"""The chat follows its run's activity feed: tool lines, milestone bands and the live row."""

import pytest

from phil.agents.fake import ScriptedAgentFactory, Turn, fire_tool
from phil.chat.controller import WAKE, _short_event
from phil.chat.events import ChatEvent
from phil.chat.state import LiveStep, SubAgent
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


AGENTS = {"main": STEP, "subs": [
    {"seq": 5, "description": "explore", "summary": "read calc.py", "started": 2.0},
    {"seq": 8, "description": "check tests", "summary": None, "started": 3.0},
]}


def test_controller_keeps_subs_and_clears_them(controller_with_run):
    """A live_agents event sets state.view().subs to SubAgent tuples; worker_lost, run_done and
    _forget_run clear them to ()."""
    controller = controller_with_run
    subs = (SubAgent(5, "explore", "read calc.py", 2.0), SubAgent(8, "check tests", None, 3.0))
    controller._handle(ChatEvent("live_agents", AGENTS))
    assert controller.state.view().subs == subs
    controller._handle(ChatEvent("worker_lost", {}))
    assert controller.state.view().subs == ()

    controller._handle(ChatEvent("live_agents", AGENTS))
    assert controller.state.view().subs == subs
    controller._handle(ChatEvent("run_done", {"state": "failed", "needs_attention": "boom"}))
    assert controller.state.view().subs == ()

    controller._handle(ChatEvent("live_agents", AGENTS))  # a failed run is still followed
    assert controller.state.view().subs == subs
    controller._forget_run()
    assert controller.state.view().subs == ()


def test_live_agents_leaves_the_live_row_to_live_step(controller_with_run):
    controller = controller_with_run
    controller._handle(ChatEvent("live_agents", AGENTS))
    assert [sub.seq for sub in controller.state.view().subs] == [5, 8]
    assert controller.state.view().live is None


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


def test_btw_sees_milestones_as_their_readable_bands():
    assert _short_event({"kind": "gate", "task": "CALC-001", "name": "green", "seq": 3}) == "✓ CALC-001 green: tests pass"
    assert _short_event({"kind": "attempt_failed", "task": "CALC-001", "attempt": 1, "limit": 3,
                         "problem": "2 tests failed", "retrying": True, "seq": 4}) == (
        "✗ CALC-001 attempt 1 failed: 2 tests failed · retrying (2 of 3)")
    assert _short_event({"kind": "node", "node": "verify"}) == "node verify"


def _end(seq: int, role: str, command: str, **extra) -> dict:
    """An activity end record for a `run_shell` call; `extra` adds fields such as `sub_id`."""
    return {"seq": seq, "ts": "2026-10-07T10:00:05Z", "phase": "end", "task": "CALC-001", "role": role,
            "tool": "run_shell", "summary": f"run {command}", "result": "→ ok", "ok": True, **extra}


def _activity(controller, *records: dict) -> str:
    controller._handle(ChatEvent("activity", {"records": list(records)}))
    return controller.console.export_text()


def test_feed_filter_shows_only_that_agent(controller_with_run):
    """/feed tester prints the 'Showing only the tester's lines…' line and sets state.view().feed_filter;
    an activity batch with an implementer end record and a tester end record prints only the tester's tool
    line; a milestone still prints; /feed then prints 'Showing everything again. 1 line from other agents
    was hidden: /show <run> to see them.' and clears feed_filter."""
    controller = controller_with_run
    controller._command("/feed tester")
    assert controller.console.export_text().strip() == "Showing only the tester's lines. /feed to show everything."
    assert controller.state.view().feed_filter == "tester"

    text = _activity(controller, _end(3, "implementer", "impl-cmd"), _end(4, "tester", "tester-cmd"))
    assert "tester-cmd" in text
    assert "impl-cmd" not in text

    controller._handle(ChatEvent("milestone", {
        "kind": "task_started", "task": "CALC-001", "title": "Add multiply", "ts": "2026-10-07T10:00:00Z", "seq": 5,
    }))
    assert "▸ CALC-001 Add multiply" in controller.console.export_text()

    controller._command("/feed")
    assert controller.console.export_text().strip() == (
        f"Showing everything again. 1 line from other agents was hidden: /show {controller._run_id} to see them."
    )
    assert controller.state.view().feed_filter is None


def test_feed_counts_several_hidden_lines(controller_with_run):
    controller = controller_with_run
    controller._command("/feed reviewer")
    controller.console.export_text()
    text = _activity(controller, _end(3, "implementer", "a"), _end(4, "tester", "b"), _end(5, "reviewer", "c"))
    assert "run   c" in text and "run   a" not in text and "run   b" not in text
    controller._command("/feed")
    assert controller.console.export_text().strip() == (
        f"Showing everything again. 2 lines from other agents were hidden: /show {controller._run_id} to see them."
    )


def test_feed_sub_agent_and_engine(controller_with_run):
    """/feed sub-agent shows only records with a sub_id; /feed engine shows only role engine; /feed implementer
    includes the implementer's sub-agent lines (role implementer, with sub_id)."""
    controller = controller_with_run
    records = (
        _end(3, "implementer", "own-cmd"),
        _end(4, "implementer", "sub-cmd", sub_id=2, sub="explore tests"),
        _end(5, "engine", "gate-cmd"),
        _end(6, "tester", "tester-cmd"),
    )

    controller._command("/feed sub-agent")
    controller.console.export_text()
    text = _activity(controller, *records)
    assert "sub-cmd" in text
    assert not any(other in text for other in ("own-cmd", "gate-cmd", "tester-cmd"))

    controller._command("/feed engine")
    controller.console.export_text()
    text = _activity(controller, *records)
    assert "gate-cmd" in text
    assert not any(other in text for other in ("own-cmd", "sub-cmd", "tester-cmd"))

    controller._command("/feed implementer")
    controller.console.export_text()
    text = _activity(controller, *records)
    assert "own-cmd" in text and "sub-cmd" in text
    assert "gate-cmd" not in text and "tester-cmd" not in text


def test_feed_filter_applies_before_folding_and_keeps_start_records(controller_with_run):
    controller = controller_with_run
    controller._command("/feed tester")
    controller.console.export_text()
    read = {"phase": "end", "task": "CALC-001", "tool": "read_file", "ok": True}
    text = _activity(
        controller,
        {**read, "seq": 3, "role": "tester", "summary": "read a.py"},
        {**read, "seq": 4, "role": "implementer", "summary": "read b.py"},
        {**read, "seq": 5, "role": "tester", "summary": "read c.py"},
        {"seq": 6, "phase": "start", "task": "CALC-001", "role": "implementer", "tool": "run_shell", "summary": "x"},
    )
    assert "read  a.py · c.py" in text  # the hidden read doesn't split the tester's fold
    assert "b.py" not in text
    controller._command("/feed")
    assert "1 line from other agents was hidden" in controller.console.export_text()  # start records aren't lines


def test_feed_unknown_and_nothing_hidden(controller_with_run):
    """/feed bogus prints 'Pick one of: implementer, tester, reviewer, architect, sub-agent, engine.' and sets
    no filter; /feed with nothing hidden prints 'Showing everything again.'"""
    controller = controller_with_run
    controller._command("/feed bogus")
    assert controller.console.export_text().strip() == (
        "Pick one of: implementer, tester, reviewer, architect, sub-agent, engine."
    )
    assert controller.state.view().feed_filter is None
    assert controller._feed_filter is None

    controller._command("/feed")
    assert controller.console.export_text().strip() == "Showing everything again."

    controller._command("/feed tester")
    controller.console.export_text()
    _activity(controller, _end(3, "tester", "tester-cmd"))
    controller._command("/feed")
    assert controller.console.export_text().strip() == "Showing everything again."


def test_the_filter_resets_when_the_run_ends(controller_with_run):
    """With /feed tester on, run_done clears the filter (state.view().feed_filter is None), and the next
    activity batch prints every line."""
    controller = controller_with_run
    controller._command("/feed tester")
    assert controller.state.view().feed_filter == "tester"
    controller._handle(ChatEvent("run_done", {"state": "failed", "needs_attention": "boom"}))
    assert controller.state.view().feed_filter is None
    assert controller._feed_filter is None
    controller.console.export_text()

    text = _activity(controller, _end(3, "implementer", "impl-cmd"), _end(4, "tester", "tester-cmd"))
    assert "impl-cmd" in text and "tester-cmd" in text


def test_forgetting_the_run_clears_the_filter(controller_with_run):
    controller = controller_with_run
    controller._command("/feed tester")
    controller._forget_run()
    assert controller.state.view().feed_filter is None
    assert controller._feed_filter is None and controller._hidden == 0


def test_help_lists_feed(controller_with_run):
    controller = controller_with_run
    controller._command("/help")
    assert "/feed <agent> (show one agent's lines; /feed to show all)" in controller.console.export_text()


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
