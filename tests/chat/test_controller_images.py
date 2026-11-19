"""Dragging an image into the chat (spec 2026-11-19 images, "Attaching an image", "Who sees the
image", "Early warning")."""

import json

import pytest

from phil.agents.pricing import PriceBook
from phil.contracts import Approach, Approaches
from tests.chat.conftest import critique, plan
from tests.chat.test_controller import run_chat, session_dir
from tests.chat.test_conversation_flow import CONVERSATION, asks, full_goal, ready
from tests.chat.test_reopen_state import reopen
from tests.helpers import TEST_MODELS

from phil.config import PhilConfig

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
BLIND = "openrouter:inclusionai/ling-3.0-flash"


@pytest.fixture
def images(tmp_path):
    """Two small images outside the repo, as a terminal pastes a dropped file: its absolute path."""
    folder = tmp_path / "Desktop"
    folder.mkdir()
    for name in ("a.png", "b.png"):
        (folder / name).write_bytes(PNG)
    return folder


@pytest.fixture
def packets(monkeypatch):
    """Every packet the chat builds, as (role, image paths)."""
    from phil.chat import design, planning

    seen: list[tuple[str, list[str]]] = []

    def spy(real):
        def build(role, contract, **kw):
            packet = real(role, contract, **kw)
            seen.append((role, list(packet.images)))
            return packet

        return build

    monkeypatch.setattr(planning, "build_packet", spy(planning.build_packet))
    monkeypatch.setattr(design, "build_packet", spy(design.build_packet))
    return seen


def _users(repo) -> list[dict]:
    lines = (session_dir(repo) / "transcript.jsonl").read_text().splitlines()
    return [record for record in map(json.loads, lines) if record.get("kind") == "user"]


def _interrupt(_controller):
    raise KeyboardInterrupt


def test_a_dragged_path_becomes_a_chip_and_is_copied(calc_repo, images):
    run_chat(calc_repo, [f"fix the layout in {images / 'a.png'}"], {"converse": [asks()]}, config=CONVERSATION)
    copy = session_dir(calc_repo) / "images" / "1-a.png"
    assert copy.read_bytes() == PNG
    [user] = _users(calc_repo)
    assert user["text"] == "fix the layout in [image 1: a.png]"
    assert user["attachments"] == [str(copy)]


def test_attachments_reach_converse_and_planning_packets(calc_repo, images, packets):
    approaches = Approaches(options=[Approach(name="Grid", summary="g"), Approach(name="Flex", summary="f")])
    run_chat(  # EOF at approval
        calc_repo, [f"fix the layout {images / 'a.png'}", f"like this {images / 'b.png'}", "1"],
        {"converse": [asks(), ready(full_goal(approach_open=True))], "design": [approaches],
         "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    folder = session_dir(calc_repo) / "images"
    a, b = str(folder / "1-a.png"), str(folder / "2-b.png")
    assert packets == [
        ("orchestrator", [a]), ("orchestrator", [a, b]), ("designer", [a, b]), ("architect", [a, b]), ("critic", [a, b]),
    ]


def test_attachments_survive_resume(calc_repo, images, packets):
    run_chat(calc_repo, [f"fix the layout {images / 'a.png'}"], {"converse": [asks()]}, config=CONVERSATION)  # EOF
    state = json.loads((session_dir(calc_repo) / "state.json").read_text())
    copy = str(session_dir(calc_repo) / "images" / "1-a.png")
    assert state["attachments"] == [copy]
    packets.clear()
    reopen(calc_repo, ["the header"], {"converse": [asks()]}, config=CONVERSATION)
    assert packets == [("orchestrator", [copy])]


def test_new_goal_starts_without_attachments(calc_repo, images, packets):
    run_chat(
        calc_repo, [f"fix the layout {images / 'a.png'}", _interrupt, "add subtract"],
        {"converse": [asks(), asks()]}, config=CONVERSATION,
    )
    assert [shown for _, shown in packets] == [[str(session_dir(calc_repo) / "images" / "1-a.png")], []]


def test_a_replaced_goal_takes_the_replacements_images(calc_repo, images, packets):
    """Typed while the first goal's job is in flight, confirmed with y: the new goal has only its own image."""
    from tests.chat.test_controller import deferred

    submit, run_next, _ = deferred()
    run_chat(
        # The first goal's route (now stale), the new goal's route, then its first turn.
        calc_repo, [f"fix {images / 'a.png'}", f"no, this {images / 'b.png'}", "y", run_next, run_next, run_next],
        {"converse": [asks()]}, config=CONVERSATION, submit=submit,
    )
    assert packets == [("orchestrator", [str(session_dir(calc_repo) / "images" / "2-b.png")])]


def test_early_warning_when_no_model_can_see(calc_repo, images, tmp_path, monkeypatch):
    listing = {"data": [{"id": "inclusionai/ling-3.0-flash", "pricing": {"prompt": "0", "completion": "0"},
                         "architecture": {"input_modalities": ["text"]}}]}
    book = PriceBook(tmp_path / "models.json", fetch=lambda: listing, clock=lambda: 1_000_000.0)
    monkeypatch.setattr("phil.agents.invoke._default_prices", book)
    config = PhilConfig(models=TEST_MODELS | {"orchestrator": BLIND}, chat={"intake": "conversation"})
    text, *_ = run_chat(
        calc_repo, [f"fix the layout {images / 'a.png'}", f"and {images / 'b.png'}"],
        {"converse": [asks(), asks()]}, config=config,
    )
    warning = f"{BLIND} can't read images. Set a vision model: [models] vision = \"provider:model\""
    assert text.count(warning) == 1


def test_no_early_warning_with_a_vision_model(calc_repo, images, tmp_path, monkeypatch):
    listing = {"data": [{"id": "inclusionai/ling-3.0-flash", "pricing": {"prompt": "0", "completion": "0"},
                         "architecture": {"input_modalities": ["text"]}}]}
    book = PriceBook(tmp_path / "models.json", fetch=lambda: listing, clock=lambda: 1_000_000.0)
    monkeypatch.setattr("phil.agents.invoke._default_prices", book)
    config = PhilConfig(
        models=TEST_MODELS | {"orchestrator": BLIND, "vision": "ollama:llava"}, chat={"intake": "conversation"}
    )
    text, *_ = run_chat(calc_repo, [f"fix {images / 'a.png'}"], {"converse": [asks()]}, config=config)
    assert "can't read images" not in text


def test_skipped_image_notice(calc_repo, images):
    with open(images / "big.png", "wb") as handle:
        handle.truncate(6 * 1024 * 1024)
    text, *_ = run_chat(
        calc_repo, [f"fix {images / 'big.png'} and {images / 'a.png'}"], {"converse": [asks()]}, config=CONVERSATION
    )
    assert "Skipped big.png: over 5 MB" in text
    [user] = _users(calc_repo)
    assert user["text"] == f"fix {images / 'big.png'} and [image 1: a.png]"
    assert user["attachments"] == [str(session_dir(calc_repo) / "images" / "1-a.png")]


def test_inline_prints_attached_line(calc_repo, images):
    text, *_ = run_chat(
        calc_repo, [f"fix {images / 'a.png'} {images / 'b.png'}"], {"converse": [asks()]}, config=CONVERSATION
    )
    assert "attached [image 1: a.png], [image 2: b.png]" in text


def test_an_agents_image_notice_prints_as_a_feed_line(calc_repo, images, monkeypatch):
    from phil.chat import controller

    def converse(ctx, turns, **kw):
        ctx.notice("↪ image: converse runs on ollama:llava")  # as VisionSwitch calls it, on the job's thread
        return asks()

    monkeypatch.setattr(controller, "converse", converse)
    text, *_ = run_chat(calc_repo, [f"fix {images / 'a.png'}"], {}, config=CONVERSATION)
    assert "↪ image: converse runs on ollama:llava" in text
    assert text.index("↪ image:") < text.index("Which module?")


def test_a_stale_agent_notice_leaves_the_cancel_pending(calc_repo):
    """After Ctrl-C, a cancelled job's `↪ image:` notice prints nothing and doesn't end "cancelling":
    the cancelled job's own result still has to land."""
    from phil.chat.controller import WAKE
    from phil.chat.events import ChatEvent
    from tests.chat.test_controller import deferred

    submit, _, _ = deferred()
    seen = {}

    def stale_notice(controller):
        controller.post(ChatEvent("agent_notice", {"line": "↪ image: route runs on x"}, controller._generation - 1))
        return WAKE

    def look(controller):
        seen["cancelling"] = controller.state.view().cancelling
        return None

    text, *_ = run_chat(calc_repo, ["fix the layout", _interrupt, stale_notice, look], {}, config=CONVERSATION,
                        submit=submit)
    assert seen == {"cancelling": True}
    assert "↪ image" not in text


def test_a_command_never_attaches(calc_repo, images):
    run_chat(calc_repo, [f"/park look at {images / 'a.png'}"], {}, config=CONVERSATION)
    assert not (session_dir(calc_repo) / "images").exists()
    [user] = _users(calc_repo)
    assert "attachments" not in user


def test_a_forced_goal_attaches(calc_repo, images, packets):
    from phil.contracts import Goal

    run_chat(  # EOF at approval
        calc_repo, [f"/full fix {images / 'a.png'}"],
        {"intake": [Goal(objective="Fix the layout")], "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    copy = str(session_dir(calc_repo) / "images" / "1-a.png")
    assert [user["text"] for user in _users(calc_repo)] == ["/full fix [image 1: a.png]"]
    assert packets == [("orchestrator", [copy]), ("architect", [copy]), ("critic", [copy])]


def test_ask_puts_the_image_in_the_answerers_packet(calc_repo, images, packets, monkeypatch):
    from phil.chat import answer
    from phil.contracts.routing import Answer

    real = answer.build_packet

    def build(role, contract, **kw):
        packet = real(role, contract, **kw)
        packets.append((role, list(packet.images)))
        return packet

    monkeypatch.setattr(answer, "build_packet", build)
    text, *_ = run_chat(calc_repo, [f"/ask what is wrong in {images / 'a.png'}"], {"answer": [Answer(text="A gap.")]},
                        config=CONVERSATION)
    assert "A gap." in text
    assert packets == [("answerer", [str(session_dir(calc_repo) / "images" / "1-a.png")])]


def test_the_answerer_is_a_vision_role():
    from phil.chat.controller import VISION_ROLES

    assert "answerer" in VISION_ROLES
