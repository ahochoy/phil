"""A goal's planning has a cost budget (chat.max_cost_usd): a live architect call once cost $3.17."""

import pytest

import phil.chat.controller as controller_mod
from phil.agents.fake import ScriptedAgentFactory
from phil.chat.planning import Planner, PlanningBudgetExceeded
from phil.config import PhilConfig
from tests.chat.conftest import critique, goal, issue, plan
from tests.chat.test_controller import run_chat
from tests.helpers import TEST_MODELS

REVISE = critique("revise", [issue("too big", "CALC-001")])


def test_a_goal_over_budget_isnt_planned(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({"architect": [plan()], "critic": [critique()]})
    with pytest.raises(PlanningBudgetExceeded):
        Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path, over_budget=lambda: True)
    assert factory.remaining() == {"architect": 1, "critic": 1}


def test_the_budget_skips_the_critics_revision_and_keeps_the_plan(chat_ctx, tmp_path):
    spent = iter([False, True])  # under budget before the first architect call, over before the revision
    factory = ScriptedAgentFactory({"architect": [plan(), plan(n=2)], "critic": [REVISE, critique()]})
    draft = Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path, over_budget=lambda: next(spent))
    assert draft.budget_stopped and len(draft.plan.tasks) == 1
    assert factory.remaining() == {"architect": 1, "critic": 1}


def test_under_budget_planning_is_unchanged(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({"architect": [plan(), plan(n=2)], "critic": [REVISE, critique()]})
    draft = Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path, over_budget=lambda: False)
    assert not draft.budget_stopped and len(draft.plan.tasks) == 2


def spend(monkeypatch, cost):
    """The goal's chat spend, as the controller reads it."""
    monkeypatch.setattr(controller_mod, "chat_cost_since", lambda *a: cost)


def test_the_chat_stops_planning_a_goal_over_its_budget(calc_repo, monkeypatch):
    spend(monkeypatch, 1.5)
    config = PhilConfig(models=TEST_MODELS, chat={"max_cost_usd": 1.0})
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]}, config=config
    )
    assert "Stopped planning: this goal has used $1.50 of its $1.00 planning budget" in text
    assert "chat.max_cost_usd" in text
    assert factory.remaining()["architect"] == 1 and spawned == []


def test_the_chat_warns_once_near_the_budget(calc_repo, monkeypatch):
    spend(monkeypatch, 0.85)
    config = PhilConfig(models=TEST_MODELS, chat={"max_cost_usd": 1.0})
    text, *_ = run_chat(
        calc_repo, ["add subtract", "n"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]}, config=config
    )
    assert text.count("Planning this goal has used $0.85 of its $1.00 budget") == 1
    assert "Plan CALC v1" in text


def test_a_skipped_revision_is_reported(calc_repo, monkeypatch):
    spent = {"cost": 0.0}

    def architect(turn):
        spent["cost"] = 2.0  # this call took the goal over its budget
        return plan()

    monkeypatch.setattr(controller_mod, "chat_cost_since", lambda *a: spent["cost"])
    config = PhilConfig(models=TEST_MODELS, chat={"max_cost_usd": 1.0})
    text, *_ = run_chat(
        calc_repo, ["add subtract", "n"], {"intake": [goal()], "architect": [architect], "critic": [REVISE]},
        config=config,
    )
    assert "Skipped the critic's revision: this goal reached its planning budget" in text
    assert "Plan CALC v1" in text


def test_an_edit_at_the_limit_keeps_the_draft(calc_repo, monkeypatch):
    spent, seen = {"cost": 0.0}, {}

    def architect(turn):
        spent["cost"] = 2.0
        return plan()

    def look(controller):
        seen["stage"], seen["draft"] = controller.stage, controller._draft
        return None

    monkeypatch.setattr(controller_mod, "chat_cost_since", lambda *a: spent["cost"])
    config = PhilConfig(models=TEST_MODELS, chat={"max_cost_usd": 1.0})
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract", "edit", "split it", look],
        {"intake": [goal()], "architect": [architect, plan(n=2)], "critic": [critique(), critique()]}, config=config,
    )
    assert "Stopped planning: this goal has used $2.00 of its $1.00 planning budget" in text
    assert seen["stage"] == "approval" and len(seen["draft"].plan.tasks) == 1
    assert factory.remaining()["architect"] == 1


def test_chat_max_cost_must_be_positive():
    with pytest.raises(ValueError):
        PhilConfig(chat={"max_cost_usd": 0})
    assert PhilConfig().chat.max_cost_usd == 1.0
