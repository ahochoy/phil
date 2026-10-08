"""The chat's status bar reflects run events it receives: this task covers the budget."""

import pytest

from phil.chat.events import ChatEvent
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import run_chat


@pytest.fixture
def controller(calc_repo):
    """A chat whose goal was approved and whose run it follows; what it printed so far is cleared."""
    box = {}

    def grab(controller):
        box["controller"] = controller
        return None  # EOF: the chat closes, still following its run

    run_chat(calc_repo, ["add subtract", "y", grab], {"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    controller = box["controller"]
    assert controller._run_id is not None
    controller.console.export_text()  # clear
    return controller


def test_the_chat_uses_the_raised_budget(controller):
    """A budget_raised event with max_cost_usd=2.4 sets controller.state.view().budget_usd == 2.4."""
    controller._handle(ChatEvent("budget_raised", {"max_cost_usd": 2.4, "max_tokens": 1600}))
    assert controller.state.view().budget_usd == 2.4
