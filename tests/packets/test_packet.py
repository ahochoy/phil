import pytest

from phil.contracts import Goal
from phil.packets import PacketTooLarge, build_packet, estimate_tokens


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
    with pytest.raises(ValueError):
        build_packet("implementer", goal(), budget_tokens=1000, root=tmp_path, files=["../secret.txt"])
    with pytest.raises(ValueError):
        build_packet("implementer", goal(), budget_tokens=1000, root=tmp_path, files=["/etc/passwd"])


def test_build_is_deterministic(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n")
    first = build_packet("implementer", goal(), budget_tokens=500, root=tmp_path, files=["a.py"], ledger=["x"])
    second = build_packet("implementer", goal(), budget_tokens=500, root=tmp_path, files=["a.py"], ledger=["x"])
    assert first.render() == second.render()
