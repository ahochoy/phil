import os

from phil.agents.fake import ScriptedAgentFactory
from tests.chat.conftest import critique, goal, plan

SCENARIOS = {
    "approve": lambda: {"intake": [goal()], "architect": [plan()], "critic": [critique()]},
}


def factory() -> ScriptedAgentFactory:
    return ScriptedAgentFactory(SCENARIOS[os.environ["PHIL_TEST_SCENARIO"]]())
