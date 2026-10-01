import os

from tests.chat.conftest import ChatFactory, critique, goal, plan

SCENARIOS = {
    "approve": lambda: {"intake": [goal()], "architect": [plan()], "critic": [critique()]},
}


def factory() -> ChatFactory:
    return ChatFactory(SCENARIOS[os.environ["PHIL_TEST_SCENARIO"]]())
