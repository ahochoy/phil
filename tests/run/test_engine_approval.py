import shlex
import sys

from tests.helpers import run_git
from tests.run.conftest import tester_report, write_green, write_red

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
        {"implementer": [red_with_build(outputs), red_with_build(outputs), write_green], "tester": [tester_report()]}
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
    harness = make_harness({"implementer": [red_with_build(outputs), write_green], "tester": [tester_report()]})
    harness.start()
    final = harness.resume({"action": "deny"})
    assert final["status"] == "completed"
    green_packet = [p for role, p in harness.factory.calls if role == "implementer"][-1]["messages"][0]["content"]
    assert f"Not approved: {BUILD}. Do not use them." in green_packet
