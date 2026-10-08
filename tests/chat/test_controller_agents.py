"""Active agents in the chat (spec 2026-10-08 agent bar): each /btw question has its own live-row line
until it lands, and a scripted run's sub-agent shows in the live row and in the feed.

Driven through `run_chat` (test_controller's harness)."""

import time
import uuid

from phil.agents.fake import ScriptedAgentFactory, Turn, fire_chain, fire_tool
from phil.chat.controller import WAKE
from phil.contracts import Brief
from phil.run.worker import run_worker
from phil.ui.toolbar import render_live_rows, toolbar_text
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import FULL_SCRIPT, deferred, run_chat
from tests.run.conftest import TEST_CMD, review, tester_report, write_green, write_red


def _labels(controller) -> list[str]:
    return [job.label for job in controller.state.view().side]


def _peek(seen: list, fn):
    def step(controller):
        seen.append(fn(controller))
        return WAKE

    return step


def test_btw_shows_a_side_line_until_answered(calc_repo):
    """/btw why did it take 3 tries? adds one SideJob whose label starts with '"why did it take 3 tries?'
    (quoted and cut to fit at 60 characters); a btw_answer event removes it; a btw_failed event removes it too."""
    submit, run_next, pending = deferred()
    seen: list = []
    long_question = "why " + "very " * 30 + "slow?"
    text, *_ = run_chat(
        calc_repo,
        [
            "/btw why did it take 3 tries?",
            _peek(seen, _labels),  # 0: the question's line, while its job is held
            run_next,  # its answer lands
            _peek(seen, _labels),  # 1
            f"/btw {long_question}",
            _peek(seen, _labels),  # 2
            run_next,  # it fails
            _peek(seen, _labels),  # 3
        ],
        {**FULL_SCRIPT, "btw": [Brief(headline="three tests were flaky"), RuntimeError("provider down")]},
        submit=submit,
    )
    assert len(seen[0]) == 1
    assert seen[0][0].startswith('"why did it take 3 tries?')
    assert seen[0][0] == '"why did it take 3 tries?"'
    assert seen[1] == []  # the answer removed it
    assert "three tests were flaky" in text
    assert len(seen[2]) == 1
    label = seen[2][0]
    assert label.startswith('"why very') and label.endswith('…"') and len(label) <= 60
    assert seen[3] == []  # the failure removed it too
    assert "Something went wrong inside Phil" in text
    assert pending == []


def _open_task(turn: Turn, run_id, description: str) -> None:
    """Start a `task` call (a sub-agent) without ending it, the way the deep agent's callbacks see one
    while the sub-agent works."""
    for callback in (turn.config or {}).get("callbacks", []):
        callback.on_tool_start({"name": "task"}, "", run_id=run_id, parent_run_id=None,
                               inputs={"description": description})


def _end_task(turn: Turn, run_id) -> None:
    for callback in (turn.config or {}).get("callbacks", []):
        callback.on_tool_end("the tests live in test_calc.py", run_id=run_id)


def test_sub_agent_end_to_end(calc_repo):
    """A scripted implementer turn calls fire_chain and fire_tool to open a task call ('explore tests') with a
    nested read_file whose parent chain leads to it, and polls the watcher while the task call is open (use an
    open start without an end for the task call, or poll between start and end): the live rows' plain
    text contains '└ sub-agent' and 'explore tests', and the printed feed line for the nested read comes
    from a record carrying sub_id."""
    holder: dict = {}
    batches: list[list[dict]] = []
    rows: list[str] = []

    def green_through_a_sub_agent(turn: Turn):
        task_id, chain_id = uuid.uuid4(), uuid.uuid4()
        _open_task(turn, task_id, "explore tests")
        fire_chain(turn, chain_id, parent_run_id=task_id)  # the sub-agent's graph, under the task call
        fire_tool(turn, "read_file", {"file_path": "test_calc.py"}, "def test_add(): ...", parent_run_id=chain_id)
        holder["controller"]._watcher.poll_once()  # the task call is still open
        _end_task(turn, task_id)
        return write_green(turn)

    def run_it(controller):
        holder["controller"] = controller
        on_activity = controller._on_activity

        def spy(data):
            batches.append(list(data.get("records", [])))
            on_activity(data)

        controller._on_activity = spy  # _handle looks the handler up on the instance
        factory = ScriptedAgentFactory({
            "implementer": [write_red, green_through_a_sub_agent], "tester": [tester_report()], "reviewer": [review()],
        })
        run_worker(calc_repo, controller._run_id, "start", factory=factory, sleep=lambda _: None)
        return WAKE  # the events the mid-turn poll posted are handled now

    def look(controller):
        rows.append(toolbar_text(render_live_rows(controller.state.view(), time.time(), 120)))
        rows.append(controller.console.export_text())
        controller._watcher.poll_once()  # the rest of the run, and its end
        return WAKE

    *_, runs, _, _ = run_chat(
        calc_repo,
        ["add subtract", "y", run_it, look],
        {"intake": [goal()], "architect": [plan(test_cmd=TEST_CMD)], "critic": [critique()]},
    )
    live, printed = rows
    assert "└ sub-agent" in live
    assert "explore tests" in live

    reads = [r for batch in batches for r in batch if r.get("phase") == "end" and r.get("tool") == "read_file"]
    assert len(reads) == 1
    assert isinstance(reads[0].get("sub_id"), int)
    assert reads[0].get("sub") == "explore tests"
    assert "read  test_calc.py" in printed  # its feed line printed while the sub-agent was open
    assert runs[0].state == "completed"
