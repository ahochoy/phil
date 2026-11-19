import base64

import pytest

from phil.contracts import Goal
from phil.packets import Packet, PacketTooLarge, build_packet, estimate_tokens
from phil.packets.packet import IMAGE_TOKENS, image_paths_section


def goal() -> Goal:
    return Goal(objective="Add a subtract function")


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_contract_only_packet_renders_contract():
    packet = build_packet("critic", goal(), budget_tokens=1000)
    text = packet.render()
    assert "## Input (Goal)" in text
    assert "Add a subtract function" in text
    assert packet.tokens == estimate_tokens(text)
    assert packet.omitted == []


def test_contract_over_budget_raises():
    with pytest.raises(PacketTooLarge):
        build_packet("critic", goal(), budget_tokens=5)


def test_files_added_in_order_until_budget(tmp_path):
    (tmp_path / "small.py").write_text("x = 1\n")
    (tmp_path / "big.py").write_text("y = 2\n" * 2000)
    (tmp_path / "tiny.py").write_text("z = 3\n")
    packet = build_packet(
        "implementer", goal(), budget_tokens=300, root=tmp_path, files=["small.py", "big.py", "tiny.py"]
    )
    assert list(packet.files) == ["small.py", "tiny.py"]
    assert packet.omitted == ["big.py"]
    assert packet.tokens <= 300


def test_missing_file_is_reported(tmp_path):
    packet = build_packet("implementer", goal(), budget_tokens=1000, root=tmp_path, files=["nope.py"])
    assert packet.omitted == ["nope.py (not found)"]


def test_ledger_trimmed_after_files(tmp_path):
    ledger = [f"assumption number {i} about the listing model" for i in range(200)]
    packet = build_packet("reviewer", goal(), budget_tokens=400, ledger=ledger)
    assert 0 < len(packet.ledger) < 200
    assert packet.ledger == ledger[: len(packet.ledger)]
    assert packet.omitted == [f"{200 - len(packet.ledger)} ledger entries"]
    assert packet.tokens <= 400


def test_paths_must_stay_under_root(tmp_path):
    packet = build_packet(
        "implementer", goal(), budget_tokens=1000, root=tmp_path, files=["../secret.txt", "/etc/passwd"]
    )
    assert packet.omitted == ["../secret.txt (outside root)", "/etc/passwd (outside root)"]
    assert packet.files == {}


def test_build_is_deterministic(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n")
    first = build_packet("implementer", goal(), budget_tokens=500, root=tmp_path, files=["a.py"], ledger=["x"])
    second = build_packet("implementer", goal(), budget_tokens=500, root=tmp_path, files=["a.py"], ledger=["x"])
    assert first.render() == second.render()


@pytest.mark.parametrize("budget", range(40, 90, 3))
@pytest.mark.parametrize("entries", [5, 50, 500])
def test_rendered_packet_never_exceeds_budget(budget, entries):
    ledger = [f"assumption {i}" for i in range(entries)]
    try:
        packet = build_packet("reviewer", goal(), budget_tokens=budget, ledger=ledger)
    except PacketTooLarge:
        return
    assert packet.tokens <= budget
    assert estimate_tokens(packet.render()) <= budget
    if len(packet.ledger) < entries:
        assert f"{entries - len(packet.ledger)} ledger entries" in packet.omitted


PNG = b"\x89PNG\r\n\x1a\nfake-bytes"


def _png(tmp_path, name="shot.png"):
    path = tmp_path / name
    path.write_bytes(PNG)
    return path


def test_images_add_tokens_and_can_overflow_the_budget(tmp_path):
    plain = build_packet("designer", goal(), budget_tokens=5000)
    with_image = build_packet("designer", goal(), budget_tokens=5000, images=[_png(tmp_path)])
    assert with_image.tokens == plain.tokens + IMAGE_TOKENS
    assert with_image.images == [str(tmp_path / "shot.png")]
    with pytest.raises(PacketTooLarge):
        build_packet("designer", goal(), budget_tokens=plain.tokens + IMAGE_TOKENS - 1, images=[_png(tmp_path)])


def test_render_content_has_text_then_image_parts(tmp_path):
    packet = build_packet("designer", goal(), budget_tokens=5000, images=[_png(tmp_path)])
    text, label, image = packet.render_content()
    assert text == {"type": "text", "text": packet.render()}
    assert label == {"type": "text", "text": "Image: shot.png"}
    assert image == {"type": "image", "base64": base64.b64encode(PNG).decode(), "mime_type": "image/png"}


def test_each_image_part_follows_a_label_with_its_original_name(tmp_path):
    before, after = _png(tmp_path, "1-before.png"), _png(tmp_path, "12-after-2.png")
    packet = build_packet("designer", goal(), budget_tokens=5000, images=[before, after])
    content = packet.render_content()
    assert [part["type"] for part in content] == ["text", "text", "image", "text", "image"]
    assert content[1] == {"type": "text", "text": "Image: before.png"}
    assert content[3] == {"type": "text", "text": "Image: after-2.png"}


def test_render_content_is_plain_text_without_images():
    packet = build_packet("designer", goal(), budget_tokens=5000)
    assert packet.render_content() == packet.render()


def test_missing_image_noted_not_crashing(tmp_path):
    gone = tmp_path / "gone.png"
    packet = build_packet("designer", goal(), budget_tokens=5000, images=[gone])
    content = packet.render_content()
    assert isinstance(content, str) and content.startswith(packet.render())
    assert "(image gone.png is no longer available)" in content


def test_a_missing_image_is_noted_beside_the_ones_that_remain(tmp_path):
    packet = build_packet("designer", goal(), budget_tokens=5000, images=[_png(tmp_path), tmp_path / "gone.png"])
    text, _label, image = packet.render_content()
    assert "(image gone.png is no longer available)" in text["text"]
    assert image["type"] == "image"


def test_old_packet_json_without_images_still_loads():
    packet = build_packet("designer", goal(), budget_tokens=5000)
    data = packet.model_dump()
    del data["images"]
    assert Packet.model_validate(data).images == []


def test_image_paths_section_lists_paths():
    assert image_paths_section(["/a/one.png", "/b/two.jpg"]) == (
        "Images the user attached (for reference; you can't open these):\n- /a/one.png\n- /b/two.jpg\n"
    )
