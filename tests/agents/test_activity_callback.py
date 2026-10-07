import uuid

from phil.agents.activity import ActivityCallback, summarize_call, summarize_result
from phil.store.activity import ActivityLog


def test_summaries_per_tool():
    assert summarize_call("read_file", {"file_path": "calc.py"}) == "read calc.py"
    assert summarize_call("edit_file", {"file_path": "calc.py", "old_string": "a", "new_string": "b"}) == "edit calc.py"
    assert summarize_call("run_shell", {"command": "pytest -q"}) == "run pytest -q"
    assert summarize_call("grep", {"pattern": "divide", "path": "src"}) == 'grep "divide" src'
    assert summarize_call("mystery", None) == "mystery"


def test_shell_result_takes_the_exit_code_and_last_line():
    output = "exit_code: 1\nfull log: /x/log\n..F\n1 failed, 7 passed in 0.31s\n"
    result, ok, detail = summarize_result("run_shell", {"command": "pytest -q"}, output)
    assert (result, ok) == ("→ 1 failed, 7 passed in 0.31s", False)
    assert "1 failed, 7 passed" in detail


def test_edit_result_counts_lines_and_keeps_a_diff():
    result, ok, detail = summarize_result(
        "edit_file", {"file_path": "calc.py", "old_string": "a\nb\n", "new_string": "a\nc\nd\n"}, "ok")
    assert (result, ok) == ("+2 −1", True)
    assert "-b" in detail and "+c" in detail


def test_reads_have_no_detail():
    assert summarize_result("read_file", {"file_path": "a.py"}, "1\tprint()") == ("", True, None)


def test_callback_records_start_and_end_including_a_nested_run(tmp_path):
    log = ActivityLog(tmp_path)
    callback = ActivityCallback(log, task="T1", role="implementer")
    parent, child = uuid.uuid4(), uuid.uuid4()
    callback.on_tool_start({"name": "task"}, "{}", run_id=parent, inputs={"description": "explore"})
    callback.on_tool_start({"name": "read_file"}, "", run_id=child, parent_run_id=parent, inputs={"file_path": "a.py"})
    callback.on_tool_end("contents", run_id=child, parent_run_id=parent)
    callback.on_tool_end("done", run_id=parent)
    records, _ = log.read()
    ends = [r for r in records if r["phase"] == "end"]
    assert [r["summary"] for r in ends] == ["read a.py", "sub-agent: explore"]
    assert all(r["task"] == "T1" and r["role"] == "implementer" for r in ends)


def test_callback_records_a_tool_error(tmp_path):
    log = ActivityLog(tmp_path)
    callback = ActivityCallback(log, task="T1", role="implementer")
    run = uuid.uuid4()
    callback.on_tool_start({"name": "edit_file"}, "", run_id=run, inputs={"file_path": "a.py"})
    callback.on_tool_error(ValueError("old_string not found\nmore"), run_id=run)
    end = log.read()[0][-1]
    assert (end["ok"], end["result"]) == (False, "old_string not found")


def test_callback_records_an_error_tool_message_as_a_failure(tmp_path):
    from langchain_core.messages import ToolMessage

    log = ActivityLog(tmp_path)
    callback = ActivityCallback(log, task="T1", role="implementer")
    run = uuid.uuid4()
    callback.on_tool_start({"name": "edit_file"}, "", run_id=run, inputs={"file_path": "a.py"})
    callback.on_tool_end(
        ToolMessage(content="Error: String not found in file", status="error", tool_call_id="x"), run_id=run)
    end = log.read()[0][-1]
    assert (end["ok"], end["result"]) == (False, "Error: String not found in file")


def test_ignored_tools_are_not_recorded(tmp_path):
    log = ActivityLog(tmp_path)
    callback = ActivityCallback(log, task=None, role="architect", ignore_tools={"PlanOutput"})
    for name in ("write_todos", "PlanOutput"):
        run = uuid.uuid4()
        callback.on_tool_start({"name": name}, "{}", run_id=run)
        callback.on_tool_end("x", run_id=run)
    assert log.read()[0] == []


def test_a_broken_log_never_breaks_the_callback(tmp_path):
    log = ActivityLog(tmp_path)
    log.disabled = True
    callback = ActivityCallback(log, task=None, role="r")
    run = uuid.uuid4()
    callback.on_tool_start({"name": "read_file"}, "", run_id=run, inputs={"file_path": "a"})
    callback.on_tool_end("x", run_id=run)  # no exception
