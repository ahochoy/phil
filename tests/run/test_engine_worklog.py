import json
import uuid

from phil.agents.fake import Turn
from phil.contracts import TaskResult, Worklog
from phil.config import PhilConfig, RoleBudget
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
    """Reads through the file tools, then returns a worklog — with a stray `files_read` the model
    isn't meant to write, which is dropped: the engine fills it from the tools."""
    _read(turn, "read_file", file_path="/calc.py")
    _read(turn, "read_file", file_path="calc.py")
    _read(turn, "ls", path="/")
    result = write_red(turn)
    worklog = {"files_read": ["/invented.py"], "files_changed": ["tests/test_sub.py"], "notes": ["wrote the subtract test"]}
    return result.model_copy(update={"worklog": Worklog.model_validate(worklog)})


def present_at_call(seen: list[bool], script):
    """Wraps a scripted turn to record whether the red test file exists when the call starts."""

    def run(turn: Turn):
        seen.append((turn.workdir / "tests" / "test_sub.py").exists())
        return script(turn)

    return run


def test_a_rejected_attempt_hands_the_next_one_a_fallback_worklog_and_the_diff(make_harness):
    seen: list[bool] = []
    harness = make_harness(
        {
            "implementer": [rejected_red, rejected_red, present_at_call(seen, write_red), write_green],
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
    assert worklog["files_read"] == ["calc.py", "tests"]
    assert len(worklog["notes"]) == 1
    assert worklog["notes"][0].startswith("implementer output rejected: no structured output")
    assert "def test_subtract" in second["diff"]
    assert "+++ b/tests/test_sub.py" in second["diff"]
    # A failed gate discards the attempt: the retry starts from the task's base, told so.
    assert (first["continuing"], second["continuing"]) == (False, False)
    assert seen == [False]


def test_an_accepted_red_worklog_and_diff_reach_the_green_packet(make_harness):
    seen: list[bool] = []
    harness = make_harness(
        {"implementer": [red_with_worklog, present_at_call(seen, write_green)], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()

    assert final["status"] == "completed"
    red, green = implement_inputs(harness)
    assert red["worklog"] is None
    assert green["phase"] == "green"
    # files_read comes from the file tools, stored repo-relative (and deduplicated) like the
    # fallback's paths; the model's own value never reaches the next attempt.
    assert green["worklog"] == {
        "files_read": ["calc.py", "."],
        "files_changed": ["tests/test_sub.py"],
        "notes": ["wrote the subtract test"],
    }
    assert "def test_subtract" in green["diff"]
    assert green["continuing"] is False
    assert seen == [True]  # the red snapshot is restored for green, as before
    # An accepted output's worklog is stored as the agent returned it, plus the engine's files_read.
    assert final["worklogs"]["CALC-001"] == {"files_read": [], "files_changed": [], "notes": []}


def test_an_approval_resume_keeps_the_worktree_and_carries_the_worklog_and_diff(make_harness, calc_repo):
    add_build_script(calc_repo)
    outputs: list[str] = []
    seen: list[bool] = []
    harness = make_harness(
        {
            "implementer": [red_with_build(outputs), present_at_call(seen, red_with_build(outputs)), write_green],
            "tester": [tester_report()],
            "reviewer": [review()],
        }
    )
    assert harness.start()["__interrupt__"][0].value["commands"] == [BUILD]
    (harness.deps.worktree / "tests" / "notes.txt").write_text("left by the first attempt\n")
    final = harness.resume({"action": "approve"})

    assert final["status"] == "completed"
    first, resumed, green = implement_inputs(harness)
    assert resumed["phase"] == "red"
    # No gate judged the approved attempt: it continues on the worktree exactly as it was left.
    assert seen == [True]
    assert (first["continuing"], resumed["continuing"], green["continuing"]) == (False, True, False)
    assert "tests/notes.txt" in resumed["diff"]
    assert final["keep_worktree"] is False
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


def test_a_diff_that_overflows_a_small_budget_is_omitted(make_harness):
    def big_red(turn: Turn) -> TaskResult:
        result = write_red(turn)
        (turn.workdir / "tests" / "data.txt").write_text("y\n" * 6000)
        return result

    config = PhilConfig(budget={"implementer": RoleBudget(max_input_tokens=1_500)})
    harness = make_harness(
        {"implementer": [big_red, write_green], "tester": [tester_report()], "reviewer": [review()]}, config=config
    )
    final = harness.start()

    assert final["status"] == "completed"
    green = implement_inputs(harness)[1]
    assert green["diff"] == "…(diff omitted: over the packet budget)"
    assert green["worklog"] is not None
