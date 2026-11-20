"""Mockups at the approach stage (spec 2026-11-20 mockups §4.1, §4.4, §4.6)."""

import pytest

from phil.chat.decision import approach_decision
from phil.contracts import Approach, Approaches, Mockup, MockupDrawing
from phil.repo import resolve_repo
from phil.store.paths import ProjectPaths
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import deferred, run_chat, session_dir

HINT = 'Say "show" to see these as mockups.'
OPEN = goal("Add a CTA section to the home page", approach_open=True)
PLANNING = {"architect": [plan()], "critic": [critique()]}


def approaches(visual: bool) -> Approaches:
    return Approaches(
        options=[
            Approach(name="Footer band", summary="A full-width band above the footer."),
            Approach(name="Inline card", summary="A card after the hero."),
        ],
        recommended=0,
        visual=visual,
    )


def drawings(*pairs: tuple[str, str], sketch: bool = False) -> Mockup:
    return Mockup(drawings=[
        MockupDrawing(name=name, **({"sketch": text} if sketch else {"html": f"<p>{text}</p>"})) for name, text in pairs
    ])


@pytest.fixture
def contracts(monkeypatch):
    """Every contract the mockup agent and the architect are sent, as (role, contract)."""
    from phil.chat import mockup, planning

    seen: list[tuple[str, object]] = []

    def spy(real):
        def build(role, contract, **kw):
            seen.append((role, contract))
            return real(role, contract, **kw)

        return build

    monkeypatch.setattr(mockup, "build_packet", spy(mockup.build_packet))
    monkeypatch.setattr(planning, "build_packet", spy(planning.build_packet))
    return seen


@pytest.fixture
def browser(monkeypatch):
    """A browser that's available and opens; `opened` is every URL it was asked to open."""
    state = {"available": True, "opens": True, "opened": []}

    def open_(url, *args, **kw):
        state["opened"].append(url)
        return state["opens"]

    monkeypatch.setattr("phil.chat.mockup_page.browser_available", lambda env, platform: state["available"])
    monkeypatch.setattr("phil.chat.controller.webbrowser.open", open_)
    return state


def mockup_contracts(seen):
    return [contract for role, contract in seen if role == "designer" and type(contract).__name__ == "MockupInput"]


def page_path(repo):
    chat_id = session_dir(repo).name
    return ProjectPaths(resolve_repo(repo).slug).project_dir / "mockups" / f"{chat_id}.html"


def flat(text: str) -> str:
    return "".join(text.split())


def test_visual_approaches_offer_the_hint(calc_repo, browser):
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add a CTA section"], {"intake": [OPEN], "design": [approaches(True)]}
    )
    assert HINT in text
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add a CTA section", "1", "n"], {"intake": [OPEN], "design": [approaches(False)], **PLANNING}
    )
    assert HINT not in text
    assert all(name != "mockup" for name, _ in factory.calls)


def test_show_opens_the_browser_once_then_asks_for_a_reload(calc_repo, browser, contracts):
    text, _, _, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "show", "show 2 less copy"],
        {"intake": [OPEN], "design": [approaches(True)],
         "mockup": [drawings(("Footer band", "band one"), ("Inline card", "card one")),
                    drawings(("Inline card", "card two"))]},
    )
    assert "Pick an approach [Enter = 1 / number / show / describe your own] › " in prompts
    assert len(browser["opened"]) == 1
    assert browser["opened"][0] == page_path(calc_repo).as_uri()
    assert "Drawing the mockup…" in text
    assert "Mockup updated: reload the tab" in text
    first, second = mockup_contracts(contracts)
    assert first.note == "" and [a.name for a in first.approaches] == ["Footer band", "Inline card"]
    assert first.format == "html"
    assert second.note == "less copy" and [a.name for a in second.approaches] == ["Inline card"]
    page = page_path(calc_repo).read_text()
    assert "card two" in page and "card one" not in page
    assert flat(page_path(calc_repo).as_uri()) in flat(text)  # the link prints every time
    assert flat(text).count(flat(page_path(calc_repo).as_uri())) == 2


def test_without_a_browser_the_sketch_prints(calc_repo, browser, contracts):
    browser["available"] = False
    sketch = "+--------+\n| BUY NOW |\n+--------+"
    text, *_ = run_chat(
        calc_repo, ["add a CTA section", "show 1"],
        {"intake": [OPEN], "design": [approaches(True)], "mockup": [drawings(("Footer band", sketch), sketch=True)]},
    )
    [contract] = mockup_contracts(contracts)
    assert contract.format == "sketch"
    assert "1. Footer band" in text
    assert "| BUY NOW |" in text
    assert browser["opened"] == []
    assert not page_path(calc_repo).exists()


def test_a_wide_sketch_is_clipped_not_wrapped(calc_repo, browser):
    browser["available"] = False
    wide = "x" * 100

    def narrow(controller):
        controller.console._console.width = 60  # a terminal narrower than the sketch
        return "show 1"

    text, *_ = run_chat(
        calc_repo, ["add a CTA section", narrow],
        {"intake": [OPEN], "design": [approaches(True)], "mockup": [drawings(("Footer band", wide), sketch=True)]},
    )
    lines = [line for line in text.splitlines() if "x" in line and set(line.strip()) == {"x"}]
    assert len(lines) == 1  # cut, not wrapped onto a second line
    assert 50 <= len(lines[0].rstrip()) < 60


def test_browser_that_wont_open_still_prints_the_link(calc_repo, browser):
    browser["opens"] = False
    text, *_ = run_chat(
        calc_repo, ["add a CTA section", "show"],
        {"intake": [OPEN], "design": [approaches(True)],
         "mockup": [drawings(("Footer band", "band"), ("Inline card", "card"))]},
    )
    assert "Couldn't open the browser; open the mockup here:" in text
    assert flat(page_path(calc_repo).as_uri()) in flat(text)


def test_show_followed_by_words_is_an_approach(calc_repo, browser, contracts):
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add a CTA section", "show prices in red", "n"],
        {"intake": [OPEN], "design": [approaches(True)], **PLANNING},
    )
    assert all(name != "mockup" for name, _ in factory.calls)
    [architect] = [contract for role, contract in contracts if role == "architect"]
    assert architect.approach_note == "show prices in red"


def test_show_out_of_range_asks_again(calc_repo, browser):
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add a CTA section", "show 3", "1", "n"],
        {"intake": [OPEN], "design": [approaches(True)], **PLANNING},
    )
    assert "Pick 1–3, or describe your own approach, or show N." in text
    assert all(name != "mockup" for name, _ in factory.calls)


def test_a_late_mockup_after_a_pick_is_dropped(calc_repo, browser):
    submit, run_next, pending = deferred()
    seen = {}

    def look(controller):
        seen["stage"], seen["mockups"] = controller.stage, dict(controller._mockups)
        return None

    text, _, _, factory, _ = run_chat(
        calc_repo,
        [
            "add a CTA section", run_next, run_next,  # routing, then intake; the design job is held
            run_next,  # the approaches arrive
            "show",  # the mockup job is held
            "1",  # pick approach 1: planning's job is held behind the mockup
            run_next,  # the mockup lands, too late
            look,
        ],
        {"intake": [OPEN], "design": [approaches(True)],
         "mockup": [drawings(("Footer band", "band"), ("Inline card", "card"))], **PLANNING},
        submit=submit,
    )
    assert [name for name, _ in factory.calls].count("mockup") == 1
    assert browser["opened"] == []
    assert "Mockup updated" not in text and "open the mockup" not in text
    assert not page_path(calc_repo).exists()
    assert seen["mockups"] == {}


def test_a_late_mockup_while_describing_is_dropped(calc_repo, browser):
    submit, run_next, pending = deferred()
    seen = {}

    def shown(controller):
        seen["show"] = controller._mockup_seq
        return "3"  # describe your own

    def look(controller):
        seen["seq"], seen["drawing"] = controller._mockup_seq, controller._drawing
        return None

    text, *_ = run_chat(
        calc_repo,
        ["add a CTA section", run_next, run_next, run_next, "show", shown, run_next, look],
        {"intake": [OPEN], "design": [approaches(True)],
         "mockup": [drawings(("Footer band", "band"), ("Inline card", "card"))]},
        submit=submit,
    )
    assert browser["opened"] == [] and not page_path(calc_repo).exists()
    assert seen["seq"] > seen["show"] and seen["drawing"] is False  # describing dropped the show's job


def test_mockup_failure_stays_at_the_approach_stage(calc_repo, browser, contracts):
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add a CTA section", "show", "1", "n"],
        {"intake": [OPEN], "design": [approaches(True)], "mockup": [RuntimeError("down")] * 3, **PLANNING},
    )
    assert "Couldn't draw the mockup:" in text and "down" in text
    [architect] = [contract for role, contract in contracts if role == "architect"]
    assert architect.chosen_approach is not None and architect.chosen_approach.name == "Footer band"


def test_a_page_that_cant_be_built_stays_at_the_approach_stage(calc_repo, browser, contracts, monkeypatch):
    def broken(*args, **kw):
        raise ValueError("bad page")

    monkeypatch.setattr("phil.chat.mockup_page.build_page", broken)
    text, *_ = run_chat(
        calc_repo, ["add a CTA section", "show", "1", "n"],
        {"intake": [OPEN], "design": [approaches(True)],
         "mockup": [drawings(("Footer band", "band"), ("Inline card", "card"))], **PLANNING},
    )
    assert "Couldn't draw the mockup:" in text
    [architect] = [contract for role, contract in contracts if role == "architect"]
    assert architect.chosen_approach is not None and architect.chosen_approach.name == "Footer band"


def test_drawings_are_kept_per_approach(calc_repo, browser):
    seen = {}

    def look(controller):
        seen["mockups"] = dict(controller._mockups)
        return None

    run_chat(
        calc_repo, ["add a CTA section", "show", "show 2 less copy", look],
        {"intake": [OPEN], "design": [approaches(True)],
         "mockup": [drawings(("Footer band", "band one"), ("Inline card", "card one"), ("Nobody", "who")),
                    drawings(("Inline card", "card two"))]},
    )
    assert set(seen["mockups"]) == {"Footer band", "Inline card"}
    assert seen["mockups"]["Footer band"].kind == "html" and "band one" in seen["mockups"]["Footer band"].text
    assert "card two" in seen["mockups"]["Inline card"].text
    kept = seen["mockups"]["Inline card"].path
    assert kept == page_path(calc_repo).with_name(f"{session_dir(calc_repo).name}-inline-card.html")
    assert kept.read_text(encoding="utf-8") == seen["mockups"]["Inline card"].text


def architect_contracts(seen):
    return [contract for role, contract in seen if role == "architect"]


def band_fragment():
    from phil.chat.mockup_page import clean_fragment

    return clean_fragment("<p>band</p>")


BOTH = {"intake": [OPEN], "design": [approaches(True)],
        "mockup": [drawings(("Footer band", "band"), ("Inline card", "card"))]}


def test_picked_approach_mockup_reaches_the_architect(calc_repo, browser, contracts):
    run_chat(calc_repo, ["add a CTA section", "show", "1", "n"], {**BOTH, **PLANNING})
    [architect] = architect_contracts(contracts)
    assert architect.mockup == band_fragment() and "band" in architect.mockup
    contracts.clear()
    run_chat(
        calc_repo, ["add a CTA section", "show 1", "2", "n"],
        {"intake": [OPEN], "design": [approaches(True)], "mockup": [drawings(("Footer band", "band"))], **PLANNING},
    )
    [architect] = architect_contracts(contracts)
    assert architect.chosen_approach.name == "Inline card" and architect.mockup == ""


def test_a_large_mockup_is_clipped_before_the_architect(calc_repo, browser, contracts):
    from phil.chat.mockup_page import CLIPPED
    from phil.contracts.design import MOCKUP_MAX_CHARS

    big = "\n".join(["<p>" + "w" * 96 + "</p>"] * 300)  # about 30,000 characters
    run_chat(
        calc_repo, ["add a CTA section", "show 1", "1", "n"],
        {"intake": [OPEN], "design": [approaches(True)],
         "mockup": [Mockup(drawings=[MockupDrawing(name="Footer band", html=big)])], **PLANNING},
    )
    [architect] = architect_contracts(contracts)
    assert architect.mockup.endswith(CLIPPED)
    assert len(architect.mockup) <= MOCKUP_MAX_CHARS + len(CLIPPED)


def test_a_large_restored_mockup_is_clipped(calc_repo, browser, contracts):
    from phil.chat.mockup_page import CLIPPED
    from phil.contracts.design import MOCKUP_MAX_CHARS
    from tests.chat.test_reopen_state import reopen

    run_chat(calc_repo, ["add a CTA section", "show"], BOTH)
    kept = page_path(calc_repo).with_name(f"{session_dir(calc_repo).name}-footer-band.html")
    kept.write_text("<p>z</p>\n" * 4000, encoding="utf-8")  # grown on disk since
    reopen(calc_repo, ["1", "n"], PLANNING)
    [architect] = architect_contracts(contracts)
    assert architect.mockup.endswith(CLIPPED) and len(architect.mockup) <= MOCKUP_MAX_CHARS + len(CLIPPED)


def test_slug_fallbacks_never_share_a_file(calc_repo, browser):
    seen = {}

    def look(controller):
        seen["mockups"] = dict(controller._mockups)
        return None

    pair = Approaches(
        options=[Approach(name="Approach 2", summary="One."), Approach(name="???", summary="Two.")], visual=True
    )
    run_chat(
        calc_repo, ["add a CTA section", "show", look],
        {"intake": [OPEN], "design": [pair], "mockup": [drawings(("Approach 2", "first"), ("???", "second"))]},
    )
    first, second = seen["mockups"]["Approach 2"].path, seen["mockups"]["???"].path
    assert first != second
    assert "first" in first.read_text() and "second" in second.read_text()


def test_a_described_approach_carries_no_mockup(calc_repo, browser, contracts):
    run_chat(calc_repo, ["add a CTA section", "show", "3", "a banner at the top", "n"], {**BOTH, **PLANNING})
    [architect] = architect_contracts(contracts)
    assert architect.approach_note == "a banner at the top" and architect.mockup == ""


def test_a_revision_keeps_the_mockup(calc_repo, browser, contracts):
    planning = {"architect": [plan(), plan()], "critic": [critique(), critique()]}
    run_chat(calc_repo, ["add a CTA section", "show", "1", "edit", "smaller", "n"], {**BOTH, **planning})
    first, revised = architect_contracts(contracts)
    assert first.mockup == revised.mockup == band_fragment()


def test_mockups_survive_reopen(calc_repo, browser, contracts):
    from tests.chat.test_reopen_state import reopen

    run_chat(calc_repo, ["add a CTA section", "show"], BOTH)  # EOF at the approach stage
    text, _, _, _, prompts = reopen(calc_repo, ["1", "n"], PLANNING)
    assert "Footer band" in text and "Inline card" in text
    assert prompts[0] == "Pick an approach [Enter = 1 / number / show / describe your own] › "
    [architect] = architect_contracts(contracts)
    assert architect.chosen_approach.name == "Footer band"
    assert architect.mockup == band_fragment()


def test_the_picked_mockup_survives_a_reopen_at_approval(calc_repo, browser, contracts):
    from tests.chat.test_reopen_state import reopen

    run_chat(calc_repo, ["add a CTA section", "show", "1"], {**BOTH, **PLANNING})  # EOF at approval
    contracts.clear()
    reopen(calc_repo, ["edit", "smaller", "n"], PLANNING)
    [revised] = architect_contracts(contracts)
    assert revised.mockup == band_fragment()


def test_a_deleted_mockup_file_means_no_mockup(calc_repo, browser, contracts):
    from tests.chat.test_reopen_state import reopen

    run_chat(calc_repo, ["add a CTA section", "show"], BOTH)
    kept = page_path(calc_repo).with_name(f"{session_dir(calc_repo).name}-footer-band.html")
    kept.unlink()
    text, *_ = reopen(calc_repo, ["1", "n"], PLANNING)
    [architect] = architect_contracts(contracts)
    assert architect.chosen_approach.name == "Footer band" and architect.mockup == ""


def test_a_saved_mockup_path_outside_the_folder_is_ignored(calc_repo, browser, contracts, tmp_path):
    import json

    from tests.chat.test_reopen_state import reopen

    run_chat(calc_repo, ["add a CTA section", "show"], BOTH)
    elsewhere = tmp_path / "secret.html"
    elsewhere.write_text("<p>secret</p>", encoding="utf-8")
    path = session_dir(calc_repo) / "state.json"
    state = json.loads(path.read_text())
    state["mockups"]["Footer band"] = str(elsewhere)
    path.write_text(json.dumps(state))
    reopen(calc_repo, ["1", "n"], PLANNING)
    [architect] = architect_contracts(contracts)
    assert architect.mockup == ""


def test_a_reopened_visual_choice_still_offers_mockups(calc_repo, browser):
    from tests.chat.test_reopen_state import reopen

    run_chat(calc_repo, ["add a CTA section"], {"intake": [OPEN], "design": [approaches(True)]})
    calls = []
    text, *_ = reopen(calc_repo, [None], choose=True, calls=calls)
    assert HINT in text
    [decision] = [call[2] for call in calls if call[0] == "choose"]
    assert "show" in [option.answer for option in decision.options]


def test_a_reopened_plain_choice_offers_no_mockups(calc_repo, browser):
    from tests.chat.test_reopen_state import reopen

    run_chat(calc_repo, ["add a CTA section"], {"intake": [OPEN], "design": [approaches(False)]})
    calls = []
    text, *_ = reopen(calc_repo, [None], choose=True, calls=calls)
    assert HINT not in text
    [decision] = [call[2] for call in calls if call[0] == "choose"]
    assert "show" not in [option.answer for option in decision.options]


def test_mockup_call_numbers_continue_after_reopen(calc_repo, browser):
    from tests.chat.test_reopen_state import reopen

    seen = []

    def count(then):
        def look(controller):
            seen.append(controller._mockup_calls)
            return then

        return look

    run_chat(calc_repo, ["add a CTA section", "show"], BOTH)
    reopen(calc_repo, [count("show 2"), count(None)], {"mockup": [drawings(("Inline card", "card two"))]})
    assert seen == [1, 2]


def test_menu_offers_show_as_mockups_when_visual():
    pairs = [("Footer band", "a band"), ("Inline card", "a card")]
    options = approach_decision(pairs, visual=True).options
    answers = [option.answer for option in options]
    assert "show" in answers
    show = answers.index("show")
    assert options[show].label == "Show as mockups"
    typed = next(i for i, option in enumerate(options) if option.typed)
    assert show < typed
    assert "show" not in [option.answer for option in approach_decision(pairs).options]
    assert "show" not in [option.answer for option in approach_decision(pairs, visual=False).options]
