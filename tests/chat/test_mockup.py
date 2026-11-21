import pytest

from phil.agents.fake import ScriptedAgentFactory, Turn
from phil.agents.invoke import AgentContext
from phil.chat.mockup import ShowRequest, draw_mockup, parse_show
from phil.config import PhilConfig
from phil.contracts import Approach, Goal, Mockup, MockupDrawing
from phil.store.db import connect

GOAL = Goal(objective="Add a CTA section to the home page", approach_open=True)
FOOTER = Approach(name="Footer band", summary="A band above the footer.")
INLINE = Approach(name="Inline card", summary="A card after the hero.")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("show", ShowRequest(None, "")),
        ("Show", ShowRequest(None, "")),
        ("show 2", ShowRequest(2, "")),
        ("show 2.", ShowRequest(2, "")),
        ("show 2)", ShowRequest(2, "")),
        ("show 2 above the footer", ShowRequest(2, "above the footer")),
        ("show prices in red", None),
        ("showcase it", None),
        ("2", None),
        ("", None),
    ],
)
def test_show_forms(text, expected):
    assert parse_show(text) == expected


def test_draw_mockup_sends_the_requested_format_and_note(tmp_path):
    seen: list[Turn] = []

    def mockup(turn: Turn) -> Mockup:
        seen.append(turn)
        return Mockup(drawings=[MockupDrawing(name="Inline card", sketch="+--+\n|  |\n+--+")])

    config = PhilConfig(models={"low": "openrouter:l", "high": "openrouter:h"})
    ctx = AgentContext(
        config=config, conn=connect(tmp_path / "t.db"), layer="chat", factory=ScriptedAgentFactory({"mockup": [mockup]})
    )
    out = draw_mockup(
        ctx, GOAL, [INLINE], tree=tmp_path, overview="Tracked files:\nindex.html", note="above the footer",
        fmt="sketch", call=1,
    )
    assert out.drawings[0].name == "Inline card"
    assert seen[0].workdir == tmp_path
    packet = seen[0].payload["messages"][0]["content"]
    assert '"format": "sketch"' in packet or '"format":"sketch"' in packet
    assert "above the footer" in packet
    assert "Inline card" in packet and "Footer band" not in packet


def test_draw_mockup_sends_the_previous_drawing(tmp_path):
    seen: list[Turn] = []

    def mockup(turn: Turn) -> Mockup:
        seen.append(turn)
        return Mockup(drawings=[MockupDrawing(name="Inline card", sketch="+--+")])

    config = PhilConfig(models={"low": "openrouter:l", "high": "openrouter:h"})
    ctx = AgentContext(
        config=config, conn=connect(tmp_path / "t.db"), layer="chat", factory=ScriptedAgentFactory({"mockup": [mockup]})
    )
    draw_mockup(
        ctx, GOAL, [INLINE], tree=tmp_path, overview="", note="sticky header", fmt="sketch", call=1,
        previous="+--OLD-DRAWING--+",
    )
    assert "+--OLD-DRAWING--+" in seen[0].payload["messages"][0]["content"]


def test_mockup_prompt_ties_the_note_to_previous():
    from importlib.resources import files

    prompt = files("phil.prompts").joinpath("mockup.md").read_text(encoding="utf-8")
    assert "keep what it doesn't mention" in prompt
    assert "`previous`" in prompt
