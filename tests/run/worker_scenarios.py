import os
import time

from phil.agents.fake import ScriptedAgentFactory
from tests.run.conftest import bad_green, review, tester_report, write_green, write_red


def _slow(turn):
    time.sleep(60)
    return write_red(turn)


SCENARIOS = {
    "happy": lambda: {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
    "escalate": lambda: {"implementer": [write_red, bad_green, bad_green, bad_green]},
    "finish_after_retry": lambda: {"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]},
    "slow": lambda: {"implementer": [_slow]},
}


def factory() -> ScriptedAgentFactory:
    return ScriptedAgentFactory(SCENARIOS[os.environ["PHIL_TEST_SCENARIO"]]())
