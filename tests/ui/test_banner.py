import pytest
from rich.cells import cell_len

from phil.ui.banner import FIRST_TIPS, GAP, NARROW_BOX, NARROW_MASCOT, TIPS, BannerFacts, banner_plain, render_banner
from phil.ui.mascot import MASCOT

FACTS = BannerFacts(
    version="0.9.0", repo="calc", branch="main", sha="a1b2c3d", base=None, dirty=2, parked=3,
    models=(("high", "claude-sonnet-5"), ("low", "gemini-3.8-flash"), ("classifier", "jev-1.13")),
    budget_usd=1.0, run=("r-4f2a", "escalated", "CALC-002 needs approval"), other_chats=("7c1e",),
    first_time=False,
)


def box_lines(lines):
    return [line.plain for line in lines if line.plain[:1] in "╭│╰"]


@pytest.mark.parametrize("width", [60, 80, 120])
@pytest.mark.parametrize("facts", [
    FACTS,
    BannerFacts(**{**FACTS.__dict__, "repo": "計算器プロジェクト", "branch": "feat/🚀-launch"}),
    BannerFacts(**{**FACTS.__dict__, "models": (("high", "an-extremely-long-model-name-" * 4),)}),
])
def test_every_box_line_has_the_same_width(width, facts):
    lines = box_lines(render_banner(facts, width))
    assert lines and len({cell_len(line) for line in lines}) == 1
    assert all(cell_len(line) <= width - 1 for line in lines)


def test_uneven_and_wider_mascots_still_line_up():
    mascot = ((("", "  ^^  "),), (("", "(o_o)  wide one"),), (("", "/"),))
    lines = box_lines(render_banner(FACTS, 100, mascot=mascot))
    assert len({cell_len(line) for line in lines}) == 1
    starts = {}
    for marker in ("Phil v", "calc @", "high ", "budget "):
        [line] = [line for line in lines if marker in line]
        starts[marker] = line.index(marker)
    art_w = len("(o_o)  wide one")
    assert starts["Phil v"] == starts["calc @"]
    assert set(starts.values()) == {2 + art_w + GAP}  # every non-empty fact starts after the art and the gap


def test_tier_edges():
    assert any("◠" in t.plain or "╭─────╮" in t.plain for t in render_banner(FACTS, NARROW_MASCOT))
    assert not any("◠" in t.plain for t in render_banner(FACTS, NARROW_MASCOT - 1))
    assert box_lines(render_banner(FACTS, NARROW_BOX))
    plain = render_banner(FACTS, NARROW_BOX - 1)
    assert not box_lines(plain) and all(cell_len(t.plain) <= NARROW_BOX - 2 for t in plain)


def test_facts_content():
    text = "\n".join(t.plain for t in render_banner(FACTS, 140))
    assert "Phil v0.9.0 · deterministic orchestration, token-efficient" in text
    assert "calc @ main a1b2c3d · ⚠ 2 uncommitted · 3 parked" in text
    assert "high claude-sonnet-5 · low gemini-3.8-flash · classifier jev-1.13" in text
    assert "budget $1.00 a run" in text


def test_base_no_classifier_no_budget():
    facts = BannerFacts(**{**FACTS.__dict__, "base": "release", "models": (("high", "a"), ("low", "b")),
                           "budget_usd": 0.0})
    text = "\n".join(t.plain for t in render_banner(facts, 140))
    assert "calc · base release a1b2c3d" in text and "uncommitted" not in text
    assert "classifier" not in text and "no run budget" in text


@pytest.mark.parametrize("run,expected", [
    (("r-1", "escalated", "CALC-002 needs approval"),
     "⏸ r-1 is waiting for you (CALC-002 needs approval) · phil attach r-1"),
    (("r-1", "failed", None), "r-1 failed · phil resume r-1"),
    (("r-1", "stopped", None), "r-1 was stopped · phil resume r-1"),
    (("r-1", "running", None), "r-1 is running · phil attach r-1"),
    (("r-1", "pending", None), "r-1 is running · phil attach r-1"),
])
def test_pick_up_states(run, expected):
    facts = BannerFacts(**{**FACTS.__dict__, "run": run})
    assert expected in [t.plain for t in render_banner(facts, 140)]


def test_other_chats_and_nothing_to_pick_up():
    two = BannerFacts(**{**FACTS.__dict__, "other_chats": ("7c1e", "9a0b"), "run": None})
    assert "2 other open chats · phil --resume 7c1e" in [t.plain for t in render_banner(two, 140)]
    none = BannerFacts(**{**FACTS.__dict__, "other_chats": (), "run": None})
    text = "\n".join(t.plain for t in render_banner(none, 140))
    assert "waiting for you" not in text and "other open chat" not in text


def _escalated(summary):
    facts = BannerFacts(**{**FACTS.__dict__, "run": ("r-1", "escalated", summary)})
    return [t.plain for t in render_banner(facts, 200) if t.plain.startswith("⏸")]


def test_the_escalation_summary_collapses_whitespace():
    assert _escalated("line one\n  line\ttwo ") == ["⏸ r-1 is waiting for you (line one line two) · phil attach r-1"]


def test_a_long_escalation_summary_is_cut_with_an_ellipsis():
    exact = "x" * 60
    assert _escalated(exact) == [f"⏸ r-1 is waiting for you ({exact}) · phil attach r-1"]
    assert _escalated("y" * 61) == [f"⏸ r-1 is waiting for you ({'y' * 59}…) · phil attach r-1"]


@pytest.mark.parametrize("summary", [None, "", " \n "])
def test_an_empty_escalation_summary_leaves_out_the_brackets(summary):
    assert _escalated(summary) == ["⏸ r-1 is waiting for you · phil attach r-1"]


def test_tips_first_time_and_returning():
    returning = [t.plain for t in render_banner(FACTS, 140)]
    assert "Type a goal to start · /btw ask while it works · /feed filter the feed · /help everything else" in returning
    first = BannerFacts(**{**FACTS.__dict__, "first_time": True})
    assert any(t.plain.startswith("Describe what you want built.") for t in render_banner(first, 140))


def _styles_at(text, index):
    return {str(span.style) for span in text.spans if span.start <= index < span.end} | (
        {str(text.style)} if text.style else set()
    )


def test_tips_drop_whole_segments_at_80_columns():
    tips = render_banner(FACTS, 80)[-1]
    assert "…" not in tips.plain
    assert tips.plain == "Type a goal to start · /btw ask while it works · /help everything else"


def test_tips_drop_feed_before_btw_then_cut():
    no_btw = render_banner(FACTS, 60)[-1].plain
    assert no_btw == "Type a goal to start · /help everything else"
    assert render_banner(FACTS, 40)[-1].plain.endswith("…")


def test_tips_name_commands_in_normal_weight():
    tips = next(t for t in render_banner(FACTS, 140) if t.plain == TIPS)
    for command in ("/btw", "/feed", "/help"):
        assert _styles_at(tips, tips.plain.index(command)) == set()
    assert _styles_at(tips, 0) == {"phil.muted"}  # "Type a goal to start"
    assert _styles_at(tips, tips.plain.index(" · ")) == {"phil.muted"}
    assert _styles_at(tips, tips.plain.index("everything else")) == {"phil.muted"}
    first = BannerFacts(**{**FACTS.__dict__, "first_time": True})
    line = next(t for t in render_banner(first, 140) if t.plain == FIRST_TIPS)
    assert _styles_at(line, line.plain.index("/help")) == set()
    assert _styles_at(line, 0) == {"phil.muted"}


def test_the_models_row_drops_whole_segments_at_80_columns():
    box = "\n".join(box_lines(render_banner(FACTS, 80)))
    assert "…" not in box
    assert "high claude-sonnet-5" in box


def test_the_mascot_is_a_tuple_of_rows_of_style_text_pairs():
    assert isinstance(MASCOT, tuple) and len(MASCOT) >= 1
    for row in MASCOT:
        assert isinstance(row, tuple)
        for pair in row:
            assert isinstance(pair, tuple) and len(pair) == 2
            assert all(isinstance(part, str) for part in pair)


@pytest.mark.parametrize("width", [-5, 0, 1])
def test_tiny_widths_still_cut_every_line(width):
    lines = render_banner(FACTS, width)
    assert lines and all(cell_len(t.plain) <= 9 for t in lines)


def test_plain_has_no_box_and_no_escape_codes():
    lines = banner_plain(FACTS)
    assert not any(ch in "".join(lines) for ch in "╭╮╰╯│") and "\x1b" not in "".join(lines)
    assert any("calc @ main a1b2c3d" in line for line in lines)


def test_plain_says_uncommitted_files_stay_out_of_runs():
    assert "calc @ main a1b2c3d · ⚠ 2 uncommitted (not included in runs) · 3 parked" in banner_plain(FACTS)
    card = "\n".join(t.plain for t in render_banner(FACTS, 140))
    assert "not included in runs" not in card
