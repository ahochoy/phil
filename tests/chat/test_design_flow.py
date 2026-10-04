"""The design step before full planning (spec §3.4)."""

from phil.contracts import Approach, Approaches
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import run_chat
from tests.chat.test_quick_flow import detectable, notes, quick_goal
from tests.chat.test_routing_flow import payloads, peek, route

APPROACHES = Approaches(
    options=[
        Approach(name="Footer band", summary="A full-width band above the footer.", tradeoffs=["Visible on every page"]),
        Approach(name="Inline card", summary="A card after the hero.", tradeoffs=["Only on the home page"]),
    ],
    recommended=1,
    reason="The home page is where visitors decide.",
)
OPEN = goal("Add a CTA section to the home page", approach_open=True)
PLANNING = {"architect": [plan()], "critic": [critique()]}


def test_an_open_approach_proposes_designs_and_the_pick_reaches_the_architect(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "2", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "1. Inline card (recommended)" in text and "2. Footer band" in text
    assert "Visible on every page" in text and "3. Describe your own" in text
    assert "Pick an approach [Enter = 1 / number / describe your own] › " in prompts
    [architect] = payloads(factory, "architect")
    assert "A full-width band above the footer." in architect and "A card after the hero." not in architect
    [chosen] = notes(calc_repo, "approach_chosen")
    assert chosen["name"] == "Footer band" and chosen["note"] == ""
    assert text.count("Goal: Add a CTA section to the home page") == 1  # not shown again before planning


def test_enter_picks_the_recommended_approach(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "A card after the hero." in payloads(factory, "architect")[0]


def test_typed_text_is_the_users_own_approach(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "a sticky banner at the top", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    [architect] = payloads(factory, "architect")
    assert "a sticky banner at the top" in architect
    assert "Footer band" not in architect and "Inline card" not in architect
    [chosen] = notes(calc_repo, "approach_chosen")
    assert chosen["name"] is None and chosen["note"] == "a sticky banner at the top"


def test_describe_your_own_by_number_asks_for_it(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "3", "a sticky banner", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "Describe your approach › " in prompts
    assert "a sticky banner" in payloads(factory, "architect")[0]


def test_an_out_of_range_number_asks_again(calc_repo):
    text, *_ = run_chat(
        calc_repo, ["add a CTA section", "7", "1", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "Pick 1–3, or describe your own approach." in text


def test_an_edit_keeps_the_chosen_approach(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "2", "edit", "make it two tasks", "n"],
        {"intake": [OPEN], "design": [APPROACHES], "architect": [plan(), plan(n=2)], "critic": [critique(), critique()]},
    )
    first, second = payloads(factory, "architect")
    assert "A full-width band above the footer." in first and "A full-width band above the footer." in second


def test_a_designer_failure_plans_directly(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "n"], {"intake": [OPEN], "design": [RuntimeError("down")] * 3, **PLANNING}
    )
    assert "Couldn't propose designs; planning directly." in text
    assert "Plan CALC v1" in text
    assert notes(calc_repo, "design_failed")


def test_full_bang_skips_the_designer_in_any_case(calc_repo):
    for prefix in ("/full!", "/FULL!"):
        text, spawned, runs, factory, prompts = run_chat(
            calc_repo, [f"{prefix} add a CTA section", "n"], {"intake": [OPEN], **PLANNING}
        )
        assert "Forced: full path, no design proposals" in text
        assert "Approaches" not in text and factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}


def test_plain_full_still_proposes_designs(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["/full add a CTA section", "1", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "1. Inline card (recommended)" in text


def test_a_clear_request_skips_the_designer(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(calc_repo, ["add subtract", "n"], {"intake": [goal()], **PLANNING})
    assert "Approaches" not in text and factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}


def test_open_questions_left_after_go_skip_the_designer(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "go", "n"],
        {"intake": [goal("Add a CTA section to the home page", approach_open=True, open_questions=["Which page?"])], **PLANNING},
    )
    assert "Approaches" not in text and factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}


def test_the_quick_path_never_designs(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["fix the typo in calc"], {"route": [route("simple_change")], "intake": [quick_goal(approach_open=True)]}
    )
    assert "Quick change:" in text and "Approaches" not in text and len(spawned) == 1


def test_ctrl_c_at_the_choice_cancels_the_goal(calc_repo):
    seen = {}

    def ctrl_c(controller):
        raise KeyboardInterrupt

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", ctrl_c, peek(seen)], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "Cancelled the current goal." in text and seen["stage"] == "idle"
    assert payloads(factory, "architect") == []
