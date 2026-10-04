import shlex
import sys

from tests.helpers import run_git
from tests.run.conftest import review, task_result, tester_report, write_green, write_red

BUILD = f"{shlex.quote(sys.executable)} tools/build.py"


def add_build_script(repo):
    (repo / "tools").mkdir()
    (repo / "tools" / "build.py").write_text("print('built ok')\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "add build script")


def red_with_build(outputs):
    def script(turn):
        outputs.append(turn.tools["run_shell"](BUILD))
        return write_red(turn)

    return script


def test_denied_command_escalates_then_approval_allows_it(make_harness, calc_repo):
    add_build_script(calc_repo)
    outputs: list[str] = []
    harness = make_harness(
        {
            "implementer": [red_with_build(outputs), red_with_build(outputs), write_green],
            "tester": [tester_report()],
            "reviewer": [review()],
        }
    )
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "approval"
    assert escalation["commands"] == [BUILD]
    assert outputs[0].startswith("DENIED:")

    final = harness.resume({"action": "approve"})
    assert final["status"] == "completed"
    assert outputs[1].startswith("exit_code: 0")
    assert "built ok" in outputs[1]
    assert final["approved"] == [BUILD]
    assert final["attempts"] == 0


def test_deny_continues_to_verify_with_a_hint(make_harness, calc_repo):
    add_build_script(calc_repo)
    outputs: list[str] = []
    harness = make_harness(
        {"implementer": [red_with_build(outputs), write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    harness.start()
    final = harness.resume({"action": "deny"})
    assert final["status"] == "completed"
    green_packet = [p for role, p in harness.factory.calls if role == "implementer"][-1]["messages"][0]["content"]
    assert f"Not approved: {BUILD}. Do not use them." in green_packet


REFUSED_CMD = "python -c 'print(1)'"


def test_refused_command_does_not_escalate(make_harness):
    outputs: list[str] = []

    def refuse_then_red(turn):
        outputs.append(turn.tools["run_shell"](REFUSED_CMD))
        return write_red(turn)

    harness = make_harness(
        {"implementer": [refuse_then_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert "__interrupt__" not in final
    assert final["status"] == "completed"
    assert outputs[0].startswith("REFUSED:")


def test_read_only_command_does_not_escalate(make_harness):
    outputs: list[str] = []

    def ls_then_red(turn):
        outputs.append(turn.tools["run_shell"]("ls src"))
        return write_red(turn)

    harness = make_harness(
        {"implementer": [ls_then_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert "__interrupt__" not in final
    assert final["status"] == "completed"
    assert not outputs[0].startswith(("DENIED:", "REFUSED:"))


def test_refused_command_reaches_the_next_attempt_as_feedback(make_harness):
    def refuse_only(turn):
        turn.tools["run_shell"](REFUSED_CMD)
        return task_result("red")

    harness = make_harness(
        {"implementer": [refuse_only, write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert final["status"] == "completed"
    second = [p for role, p in harness.factory.calls if role == "implementer"][1]["messages"][0]["content"]
    assert "refused command: python -c 'print(1)'" in second


def build_only(turn):
    turn.tools["run_shell"](BUILD)
    return task_result("red")  # writes nothing


def build_only_after_approval(turn):
    turn.tools["run_shell"](BUILD)  # approved now; still writes nothing
    return task_result("red")


def red_unchanged(turn):
    return task_result("red", ["tests/test_sub.py"], ["tests/test_sub.py"])  # continues; writes nothing more


def test_an_approval_resume_that_changed_nothing_since_the_task_began_fails(make_harness, calc_repo):
    from phil.config import PhilConfig

    add_build_script(calc_repo)
    config = PhilConfig.model_validate({"run": {"max_attempts_per_phase": 1}})
    harness = make_harness({"implementer": [build_only, build_only_after_approval]}, config=config)
    assert harness.start()["__interrupt__"][0].value["reason"] == "approval"

    escalation = harness.resume({"action": "approve"})["__interrupt__"][0].value
    assert escalation["reason"] == "attempts"
    assert escalation["problems"] == ["no changes were made for CALC-001"]


def test_an_approval_resume_keeps_what_the_paused_call_wrote(make_harness, calc_repo):
    add_build_script(calc_repo)
    outputs: list[str] = []
    harness = make_harness({
        "implementer": [red_with_build(outputs), red_unchanged, write_green],
        "tester": [tester_report()],
        "reviewer": [review()],
    })
    assert harness.start()["__interrupt__"][0].value["reason"] == "approval"

    final = harness.resume({"action": "approve"})
    assert final["status"] == "completed"
    assert harness.factory.remaining() == {"implementer": 0, "tester": 0, "reviewer": 0}
