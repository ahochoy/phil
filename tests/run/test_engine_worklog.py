import json
import uuid

from phil.agents.fake import Turn
from phil.contracts import TaskResult, Worklog
from tests.run.conftest import review, tester_report, write_green, write_red
from tests.run.test_engine_approval import BUILD, add_build_script, red_with_build


def implement_inputs(harness) -> list[dict]:
    """The ImplementInput JSON of every implementer call, in order (not the in-call output retries)."""
    inputs = []
    for role, payload in harness.factory.calls:
        if role != "implementer" or len(payload["messages"]) > 1:
            continue
        content = payload["messages"][0]["content"]
        body = content.split("```json\n", 1)[1].split("\n```", 1)[0]
        inputs.append(json.loads(body))
    return inputs


def _read(turn: Turn, tool: str, **args: str) -> None:
    for callback in (turn.config or {}).get("callbacks", []):
        callback.on_tool_start({"name": tool}, json.dumps(args), run_id=uuid.uuid4())


def rejected_red(turn: Turn) -> None:
    """Reads a file, writes a test, and returns no structured output (a rejected attempt)."""
    _read(turn, "read_file", file_path="/calc.py")
    _read(turn, "ls", path="/tests")
    write_red(turn)
    return None


def red_with_worklog(turn: Turn) -> TaskResult:
    result = write_red(turn)
    worklog = Worklog(files_read=["/calc.py"], files_changed=["tests/test_sub.py"], notes=["wrote the subtract test"])
    return result.model_copy(update={"worklog": worklog})


def test_a_rejected_attempt_hands_the_next_one_a_fallback_worklog_and_the_diff(make_harness):
    harness = make_harness(
        {
            "implementer": [rejected_red, rejected_red, write_red, write_green],
            "tester": [tester_report()],
            "reviewer": [review()],
        }
    )
    final = harness.start()

    assert final["status"] == "completed"
    first, second, *_ = implement_inputs(harness)
    assert first["worklog"] is None and first["diff"] == ""
    worklog = second["worklog"]
    assert worklog["files_changed"] == ["tests/test_sub.py"]
    assert worklog["files_read"] == ["/calc.py", "/tests"]
    assert len(worklog["notes"]) == 1
    assert worklog["notes"][0].startswith("implementer output rejected: no structured output")
    assert "def test_subtract" in second["diff"]
    assert "+++ b/tests/test_sub.py" in second["diff"]


def test_an_accepted_red_worklog_and_diff_reach_the_green_packet(make_harness):
    harness = make_harness(
        {"implementer": [red_with_worklog, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()

    assert final["status"] == "completed"
    red, green = implement_inputs(harness)
    assert red["worklog"] is None
    assert green["phase"] == "green"
    assert green["worklog"] == {
        "files_read": ["/calc.py"],
        "files_changed": ["tests/test_sub.py"],
        "notes": ["wrote the subtract test"],
    }
    assert "def test_subtract" in green["diff"]
    # An accepted output's worklog is stored as the agent returned it.
    assert final["worklogs"]["CALC-001"] == {"files_read": [], "files_changed": [], "notes": []}


def test_an_approval_resume_carries_the_worklog_and_diff(make_harness, calc_repo):
    add_build_script(calc_repo)
    outputs: list[str] = []
    harness = make_harness(
        {
            "implementer": [red_with_build(outputs), red_with_build(outputs), write_green],
            "tester": [tester_report()],
            "reviewer": [review()],
        }
    )
    assert harness.start()["__interrupt__"][0].value["commands"] == [BUILD]
    final = harness.resume({"action": "approve"})

    assert final["status"] == "completed"
    _, resumed, _ = implement_inputs(harness)
    assert resumed["phase"] == "red"
    assert resumed["worklog"] == {"files_read": [], "files_changed": [], "notes": []}
    assert "def test_subtract" in resumed["diff"]


def test_a_long_diff_is_truncated(make_harness):
    def big_red(turn: Turn) -> TaskResult:
        result = write_red(turn)
        (turn.workdir / "tests" / "data.txt").write_text("x" * 100 + "\n" + "y\n" * 6000)
        return result

    harness = make_harness(
        {"implementer": [big_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()

    assert final["status"] == "completed"
    green = implement_inputs(harness)[1]
    assert green["diff"].endswith("…(diff truncated)")
    assert len(green["diff"]) <= 8_000 + len("\n…(diff truncated)")
