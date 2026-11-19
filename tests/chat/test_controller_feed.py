"""The chat follows its run's activity feed: tool lines, milestone bands and the live row."""

import pytest

from phil.agents.fake import ScriptedAgentFactory, Turn, fire_tool
from phil.chat.controller import WAKE, _short_event
from phil.chat.events import ChatEvent
from phil.chat.state import LiveStep, SubAgent
from phil.run.worker import run_worker
from phil.store.activity import ActivityLog
from phil.store.paths import ProjectPaths
from phil.ui.feed_view import FeedRenderer
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


def test_calls_roll_live_and_a_milestone_prints_the_bursts_summary_first(controller_with_run):
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
    assert "▸ CALC-001 Add multiply" in text and "pytest -q" not in text
    assert controller.state.view().rolling == ("    run    pytest -q  → 7 passed · 1.2s",)
    controller._handle(ChatEvent("milestone", {"kind": "gate", "task": "CALC-001", "name": "green", "seq": 3}))
    text = controller.console.export_text()
    assert text.index("implementer ran 1 tool · ran pytest -q  (/more #3)") < text.index("✓ CALC-001 green")
    assert controller.state.view().rolling == ()


def test_a_failed_call_prints_at_once(controller_with_run):
    text = _activity(controller_with_run, {**_end(3, "implementer", "pytest -q"), "ok": False, "result": "→ 1 failed"})
    assert "run   pytest -q  → 1 failed" in text


def test_the_run_end_prints_an_open_bursts_summary_once_before_its_notice(controller_with_run):
    controller = controller_with_run
    _activity(controller, _end(3, "implementer", "make"))
    controller._handle(ChatEvent("run_done", {"state": "failed", "needs_attention": "boom"}))
    text = controller.console.export_text()
    assert text.index("implementer ran 1 tool · ran make") < text.index("failed: boom")
    assert text.count("implementer ran 1 tool") == 1
    assert controller.state.view().rolling == ()


def test_a_pause_ends_the_burst_before_its_notice(controller_with_run):
    controller = controller_with_run
    _activity(controller, _end(3, "implementer", "make"))
    controller._handle(ChatEvent("run_paused", {"escalation": {"summary": "CALC-001 failed", "options": ["abort"]}}))
    text = controller.console.export_text()
    assert text.index("implementer ran 1 tool · ran make") < text.index("needs you: CALC-001 failed")


def test_more_hash_prints_a_bursts_calls_then_its_last_steps_detail(controller_with_run):
    controller = controller_with_run
    run_dir = ProjectPaths(controller.info.slug).run_dir(controller._run_id)
    ActivityLog(run_dir).end(4, task="CALC-001", role="implementer", tool="run_shell", summary="run make",
                             result="→ ok", ok=True, detail="made it\n", duration_ms=10)
    _activity(controller, _end(3, "implementer", "npm ci"), _end(4, "implementer", "make", detail="4.txt"))
    _flush(controller)
    controller._more_command("#4")
    text = controller.console.export_text()
    assert text.index("run   npm ci  → ok") < text.index("run   make  → ok") < text.index("made it")
    assert "#4 has no details." not in text


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


def test_worker_lost_ends_the_open_burst_before_its_notice(controller_with_run):
    controller = controller_with_run
    _activity(controller, _end(3, "implementer", "make"))
    assert controller.state.view().rolling != ()
    controller._handle(ChatEvent("worker_lost", {}))
    text = controller.console.export_text()
    assert text.count("implementer ran 1 tool · ran make") == 1
    assert text.index("implementer ran 1 tool") < text.index("stopped responding")
    assert controller.state.view().rolling == ()


def test_a_multi_line_command_and_an_escape_stay_on_one_line_each(controller_with_run):
    controller = controller_with_run
    heredoc = "cat <<EOF > a.txt\nline one\n\x1b[31mred\x1b[0m\nEOF"
    text = _activity(controller, _end(3, "implementer", heredoc))
    assert "\x1b" not in text
    rolling = controller.state.view().rolling
    assert len(rolling) == 1 and "\n" not in rolling[0] and "\x1b" not in rolling[0]
    summary = _flush(controller)
    [line] = [line for line in summary.splitlines() if "implementer ran 1 tool" in line]
    assert "ran cat <<EOF > a.txt line one" in line


def test_forgetting_the_run_clears_the_live_step_and_the_feed(controller_with_run):
    controller = controller_with_run
    controller._handle(ChatEvent("milestone", {"kind": "task_started", "task": "CALC-001", "title": "x",
                                               "ts": "2026-10-07T10:00:00Z"}))
    controller._handle(ChatEvent("live_step", STEP))
    feed = controller._feed
    controller._forget_run()
    assert controller.state.view().live is None
    assert controller.state.view().rolling == ()
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


def _flush(controller) -> str:
    """A milestone ends the open burst; returns what printed since the last export."""
    controller._handle(ChatEvent("milestone", {"kind": "task_done", "task": "CALC-001", "seq": 99}))
    return controller.console.export_text()


def test_feed_filter_shows_only_that_agent(controller_with_run):
    """/feed tester prints the 'Showing only the tester's lines…' line and sets state.view().feed_filter;
    an activity batch with an implementer end record and a tester end record prints only the tester's tool
    line; a milestone still prints; /feed then prints 'Showing everything again. 1 step from other agents
    was hidden.' and clears feed_filter."""
    controller = controller_with_run
    controller._command("/feed tester")
    assert controller.console.export_text().strip() == "Showing only the tester's lines. /feed to show everything."
    assert controller.state.view().feed_filter == "tester"

    _activity(controller, _end(3, "implementer", "impl-cmd"), _end(4, "tester", "tester-cmd"))
    controller._handle(ChatEvent("milestone", {
        "kind": "task_started", "task": "CALC-001", "title": "Add multiply", "ts": "2026-10-07T10:00:00Z", "seq": 5,
    }))
    text = controller.console.export_text()
    assert "tester ran 1 tool · ran tester-cmd" in text
    assert "impl-cmd" not in text
    assert "▸ CALC-001 Add multiply" in text

    controller._command("/feed")
    assert controller.console.export_text().strip() == "Showing everything again. 1 step from other agents was hidden."
    assert controller.state.view().feed_filter is None


def test_feed_counts_several_hidden_steps(controller_with_run):
    controller = controller_with_run
    controller._command("/feed reviewer")
    controller.console.export_text()
    text = _activity(controller, _end(3, "implementer", "a"), _end(4, "tester", "b"), _end(5, "reviewer", "c"))
    text += _flush(controller)  # summaries print both as a batch closes a burst and at the milestone
    assert "reviewer ran 1 tool · ran c" in text and "ran a" not in text and "ran b" not in text
    controller._command("/feed")
    assert controller.console.export_text().strip() == "Showing everything again. 2 steps from other agents were hidden."


def test_switching_filters_reports_what_the_old_one_hid(controller_with_run):
    controller = controller_with_run
    controller._command("/feed tester")
    _activity(controller, _end(3, "implementer", "a"), _end(4, "reviewer", "b"))
    controller.console.export_text()
    controller._command("/feed implementer")
    assert controller.console.export_text().strip().splitlines() == [
        "Showing everything again. 2 steps from other agents were hidden.",
        "Showing only the implementer's lines. /feed to show everything.",
    ]
    assert controller.state.view().feed_filter == "implementer" and controller._hidden == 0

    controller._command("/feed reviewer")  # nothing hidden: no count line
    assert controller.console.export_text().strip() == "Showing only the reviewer's lines. /feed to show everything."


def test_feed_without_a_run(controller_without_run):
    controller = controller_without_run
    controller._command("/feed tester")
    assert controller.console.export_text().strip() == "No run to filter yet."
    assert controller.state.view().feed_filter is None and controller._feed_filter is None


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
    controller._feed = FeedRenderer()
    text = _activity(controller, *records) + _flush(controller)
    assert "implementer ran 2 tools · ran sub-cmd" in text
    assert not any(other in text for other in ("own-cmd", "gate-cmd", "tester-cmd"))

    controller._command("/feed engine")
    controller.console.export_text()
    controller._feed = FeedRenderer()
    text = _activity(controller, *records) + _flush(controller)
    assert "gate-cmd" in text
    assert not any(other in text for other in ("implementer ran", "sub-cmd", "tester-cmd"))

    controller._command("/feed implementer")
    controller.console.export_text()
    controller._feed = FeedRenderer()
    text = _activity(controller, *records) + _flush(controller)
    assert "implementer ran 2 tools" in text
    assert "gate-cmd" not in text and "tester-cmd" not in text


def test_feed_hides_other_agents_summaries_and_counts_only_end_records(controller_with_run):
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
    assert text.count("tester ran 1 tool") == 2
    assert "implementer ran" not in text
    controller._command("/feed")
    assert "1 step from other agents was hidden." in controller.console.export_text()  # start records aren't steps


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
    text += _flush(controller)
    assert "ran impl-cmd" in text and "ran tester-cmd" in text


def test_forgetting_the_run_clears_the_filter(controller_with_run):
    controller = controller_with_run
    controller._command("/feed tester")
    controller._forget_run()
    assert controller.state.view().feed_filter is None
    assert controller._feed_filter is None and controller._hidden == 0


def test_help_lists_feed(controller_with_run):
    controller = controller_with_run
    controller._command("/help")
    shown = " ".join(controller.console.export_text().split())  # wherever the help text wraps
    assert "/feed <agent> show one agent's lines; /feed to show all" in shown
    assert "Ctrl+Enter or Option/Alt+Enter starts a new line" in shown


class RecordingOutput:
    """A task output that records each call as a tuple."""

    def __init__(self):
        self.calls = []

    def task_started(self, task, band):
        self.calls.append(("task_started", task, band))

    def task_line(self, task, line):
        self.calls.append(("task_line", task, line))

    def sub_agent(self, task, seq, description):
        self.calls.append(("sub_agent", task, seq, description))

    def task_done(self, task, folded):
        self.calls.append(("task_done", task, folded))


def _with_output(controller):
    out = RecordingOutput()
    controller.io.task_output = out
    return out


def _task_started(controller, task="Task 1", title="Theme setting"):
    controller._handle(ChatEvent("milestone", {
        "kind": "task_started", "task": task, "title": title, "ts": "2026-10-07T10:00:00Z", "seq": 2}))


def _task_events(controller):
    _task_started(controller)
    controller._handle(ChatEvent("activity", {"records": [
        {**_end(3, "implementer", "pytest -q"), "task": "Task 1"}]}))  # one call: its summary prints at the done
    controller._handle(ChatEvent("milestone", {
        "kind": "task_done", "task": "Task 1", "files": 2, "ts": "2026-10-07T10:00:38Z", "seq": 4}))


def test_feed_goes_to_task_output_when_given(controller_with_run):
    controller = controller_with_run
    out = _with_output(controller)
    _task_events(controller)
    assert [call[0] for call in out.calls] == ["task_started", "task_line", "task_line", "task_done"]
    assert out.calls[0][2].plain == "▸ Task 1 Theme setting"
    assert out.calls[1][2].plain == ""  # the burst summary starts a block: one empty line before it
    assert "implementer ran 1 tool" in out.calls[2][2].plain
    assert out.calls[-1][2].plain.startswith("✓ Task 1 · Theme setting · done")
    assert controller.console.export_text() == ""  # nothing printed for the task


def test_without_task_output_the_feed_prints_as_today(controller_with_run):
    controller = controller_with_run
    _task_events(controller)
    text = controller.console.export_text()
    assert "▸ Task 1 Theme setting" in text
    assert "implementer ran 1 tool" in text
    assert "✓ Task 1 done · 2 files · 38s" in text


def test_sub_agent_lines_are_announced_once_and_their_tool_lines_dropped(controller_with_run):
    controller = controller_with_run
    out = _with_output(controller)
    controller._handle(ChatEvent("live_agents", {"main": None, "subs": [{"seq": 9, "description": "early"}]}))
    assert out.calls == []  # no task is open yet
    _task_started(controller)
    subs = {"main": None, "subs": [
        {"seq": 1, "description": "find where the theme is applied"},
        {"seq": 2, "description": "check existing settings tests"}]}
    controller._handle(ChatEvent("live_agents", subs))
    controller._handle(ChatEvent("live_agents", subs))
    controller._handle(ChatEvent("activity", {"records": [
        {**_end(5, "implementer", "grep theme", sub_id=1), "task": "Task 1", "ok": False}]}))
    assert [c for c in out.calls if c[0] == "sub_agent"] == [
        ("sub_agent", "Task 1", 1, "find where the theme is applied"),
        ("sub_agent", "Task 1", 2, "check existing settings tests")]
    assert [c[0] for c in out.calls if c[0] != "sub_agent"] == ["task_started"]
    assert controller.console.export_text() == ""


def test_untasked_items_still_print_with_a_task_output(controller_with_run):
    controller = controller_with_run
    out = _with_output(controller)
    controller._handle(ChatEvent("milestone", {"kind": "rule_saved", "line": "Saved a rule", "seq": 2}))
    controller._handle(ChatEvent("activity", {"records": [
        {**_end(3, "engine", "pytest -q"), "task": None}]}))
    assert out.calls == []
    text = controller.console.export_text()
    assert "Saved a rule" in text and "pytest -q" in text


def test_the_open_task_clears_on_its_done_and_with_the_feed(controller_with_run):
    controller = controller_with_run
    out = _with_output(controller)
    _task_events(controller)
    controller._handle(ChatEvent("live_agents", {"main": None, "subs": [{"seq": 1, "description": "late"}]}))
    assert not any(c[0] == "sub_agent" for c in out.calls)
    _task_started(controller, "Task 2", "Next")
    controller._forget_run()
    controller._handle(ChatEvent("live_agents", {"main": None, "subs": [{"seq": 1, "description": "late"}]}))
    assert not any(c[0] == "sub_agent" for c in out.calls)


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
    assert "implementer ran " in text and "(/more #" in text
    assert "✓ CALC-001 done" in text


def test_feed_blocks_are_spaced_and_gate_lines_are_not(controller_with_run):
    controller = controller_with_run
    _task_started(controller, "CALC-001", "Add multiply")
    for record in (_end(3, "engine", "pytest -q"), _end(4, "implementer", "make")):  # engine: its gate run
        controller._handle(ChatEvent("activity", {"records": [record]}))
    controller._handle(ChatEvent("milestone", {
        "kind": "task_done", "task": "CALC-001", "ts": "2026-10-07T10:00:38Z", "seq": 9}))
    lines = controller.console.export_text().splitlines()
    assert len(lines) == 7, lines
    assert lines[0] == "" and lines[1].startswith("▸ CALC-001 Add multiply")
    assert "pytest -q" in lines[2]  # no blank between the band and its gate line
    assert lines[3] == "" and "implementer ran 1 tool · ran make" in lines[4]
    assert lines[5] == "" and lines[6].startswith("✓ CALC-001 done")


def test_a_commands_lines_are_one_block_spaced_from_the_typed_line(calc_repo):
    text, *_ = run_chat(calc_repo, ["/status", "/status"], {})
    status = "calc @ main\nmodel: not set\nrun: no run\n0 parked\n"
    # Each typed /status is above its block (on the terminal, not in this console): one blank line
    # after it, none inside the block, and never two in a row.
    assert text == f"\n{status}\n\n{status}\n"  # each prompt (not in this console) has a blank above it


def test_one_blank_line_between_a_block_and_the_inline_prompt_below_it(calc_repo):
    status = "calc @ main\nmodel: not set\nrun: no run\n0 parked\n"
    seen = []
    run_chat(calc_repo, ["/status", lambda controller: seen.append(controller.console.export_text())], {})
    assert seen == [f"\n{status}\n"]  # the block, then one blank line, then the prompt


def test_no_blank_line_before_the_prompt_in_the_frame(calc_repo):
    """The frame's echo has its own top margin: a blank line before the prompt would double it."""
    seen = []

    def prompt_with_frame(controller):
        controller.io.task_output = object()  # what marks the full-screen frame
        return "/status"

    run_chat(calc_repo, [prompt_with_frame, lambda controller: seen.append(controller.console.export_text())], {})
    assert seen == ["\ncalc @ main\nmodel: not set\nrun: no run\n0 parked\n"]


def test_a_hidden_burst_hides_its_blank_line_too(controller_with_run):
    controller = controller_with_run
    run_dir = ProjectPaths(controller.info.slug).run_dir(controller._run_id)
    ActivityLog(run_dir).end(5, task="CALC-001", role="implementer", tool="edit_file", summary="edit a.py",
                             result="+1 −1", ok=True, detail="--- before\n+++ after\n@@\n-old\n+new\n",
                             duration_ms=10)
    controller._command("/feed tester")
    controller.console.export_text()
    edit = {**_end(5, "implementer", "x"), "tool": "edit_file", "summary": "edit a.py", "result": "+1 −1",
            "detail": "5.txt"}
    _activity(controller, edit)
    controller._end_burst()
    assert controller.console.export_text() == ""  # not even its blank line
    controller._handle(ChatEvent("milestone", {"kind": "task_done", "task": "CALC-001", "seq": 9}))
    text = controller.console.export_text()
    assert text.startswith("\n✓ CALC-001 done")  # one blank before the next block, not two


def test_a_burst_summary_prints_its_diff_from_the_runs_detail(controller_with_run):
    controller = controller_with_run
    run_dir = ProjectPaths(controller.info.slug).run_dir(controller._run_id)
    ActivityLog(run_dir).end(5, task="CALC-001", role="implementer", tool="edit_file", summary="edit a.py",
                             result="+1 −1", ok=True, detail="--- before\n+++ after\n@@\n-old\n+new\n",
                             duration_ms=10)
    edit = {**_end(5, "implementer", "x"), "tool": "edit_file", "summary": "edit a.py", "result": "+1 −1",
            "detail": "5.txt"}
    _activity(controller, edit)
    text = _flush(controller)
    assert "implementer ran 1 tool · edited 1 file  (/more #5)" in text
    assert text.index("a.py  +1 −1") < text.index("− old") < text.index("+ new")
