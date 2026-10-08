import os
import shlex
import sys
import time

from phil.agents.fake import ScriptedAgentFactory
from tests.run.conftest import bad_green, review, tester_report, write_green, write_red


def _slow(turn):
    # Short sleeps, like a streaming agent call that keeps returning to Python. One long
    # time.sleep would be a single C call that a stop request on Windows can't wake:
    # interrupt_main(SIGTERM) only runs the handler once the main thread is back in Python.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        time.sleep(0.1)
    return write_red(turn)


def _crash(turn):
    raise RuntimeError("boom")


BUILD = f"{shlex.quote(sys.executable)} tools/build.py"  # off [shell] allow: it needs approval


def _red_with_build(turn):
    turn.tools["run_shell"](BUILD)  # denied before it's approved: the run pauses for approval
    return write_red(turn)


def _red_with_approved_build(turn):
    output = turn.tools["run_shell"](BUILD)
    if not output.startswith("exit_code: 0") or "built ok" not in output:
        raise RuntimeError(f"the approved command didn't run: {output}")
    return write_red(turn)


SCENARIOS = {
    "happy": lambda: {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
    "escalate": lambda: {"implementer": [write_red, bad_green, bad_green, bad_green]},
    "finish_after_retry": lambda: {"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]},
    "slow": lambda: {"implementer": [_slow]},
    "crash": lambda: {"implementer": [_crash]},
    "needs_approval": lambda: {"implementer": [_red_with_build]},
    "after_approval": lambda: {
        "implementer": [_red_with_approved_build, write_green], "tester": [tester_report()], "reviewer": [review()],
    },
}


def factory() -> ScriptedAgentFactory:
    return ScriptedAgentFactory(SCENARIOS[os.environ["PHIL_TEST_SCENARIO"]]())
