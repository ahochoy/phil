import os

import pytest

from phil.skills.discover import Skill
from phil.skills.tool import MAX_FILE_BYTES, MAX_LOADS, make_load_skill_tool


def make_skill(root, name="demo", body="Do the steps.\n") -> Skill:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8")
    return Skill(name, "d", directory.resolve(), "agents", "0" * 64)


def test_loads_skill_md(tmp_path):
    skill = make_skill(tmp_path, body="Do the steps.\n")
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    assert tool.__name__ == "load_skill"
    assert "Do the steps." in tool("demo")


def test_loads_a_reference_file(tmp_path):
    skill = make_skill(tmp_path)
    (skill.path / "ref").mkdir()
    (skill.path / "ref" / "a.md").write_text("reference text", encoding="utf-8")
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    assert tool("demo", "ref/a.md") == "reference text"


def test_symlink_to_a_file_inside_the_skill_is_allowed(tmp_path):
    skill = make_skill(tmp_path)
    (skill.path / "real.md").write_text("inside", encoding="utf-8")
    os.symlink(skill.path / "real.md", skill.path / "link.md")
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    assert tool("demo", "link.md") == "inside"


def test_file_outside_the_skill_is_refused(tmp_path):
    skill = make_skill(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    os.symlink(secret, skill.path / "sneaky.md")
    tool = make_load_skill_tool([skill], role="coder", notice=None)

    for requested in ("../secret.txt", str(secret), "sneaky.md"):
        result = tool("demo", requested)
        assert result.startswith(f"Can't read {requested} from demo: ")
        assert "TOP SECRET" not in result


def test_skill_md_symlinked_outside_is_refused(tmp_path):
    skill = make_skill(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    (skill.path / "SKILL.md").unlink()
    os.symlink(secret, skill.path / "SKILL.md")
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    result = tool("demo")
    assert result.startswith("Can't read SKILL.md from demo: ")
    assert "TOP SECRET" not in result


def test_missing_file_and_directory_are_refused(tmp_path):
    skill = make_skill(tmp_path)
    (skill.path / "ref").mkdir()
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    assert tool("demo", "nope.md").startswith("Can't read nope.md from demo: ")
    assert tool("demo", "ref").startswith("Can't read ref from demo: ")


def test_binary_file_is_refused(tmp_path):
    skill = make_skill(tmp_path)
    (skill.path / "logo.bin").write_bytes(b"\xff\xfe\x00\x01")
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    assert tool("demo", "logo.bin").startswith("Can't read logo.bin from demo: ")


def test_large_file_is_truncated(tmp_path):
    skill = make_skill(tmp_path)
    (skill.path / "big.txt").write_text("a" * (MAX_FILE_BYTES + 3000), encoding="utf-8")
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    result = tool("demo", "big.txt")
    assert result == "a" * MAX_FILE_BYTES + "\n[truncated: 3 KB more]"


def test_truncation_keeps_utf8_whole(tmp_path):
    skill = make_skill(tmp_path)
    (skill.path / "wide.txt").write_text("é" * MAX_FILE_BYTES, encoding="utf-8")  # 2 bytes each
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    result = tool("demo", "wide.txt")
    assert result.startswith("é" * 100)
    assert "�" not in result
    assert "[truncated: " in result


def test_fourth_load_is_refused(tmp_path):
    skill = make_skill(tmp_path)
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    for _ in range(MAX_LOADS):
        assert "Do the steps." in tool("demo")
    assert tool("demo") == "Skill limit reached (3 per task); continue without more skills."


def test_refused_calls_do_not_count(tmp_path):
    skill = make_skill(tmp_path)
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    for _ in range(5):
        tool("nope")
        tool("demo", "../x")
    for _ in range(MAX_LOADS):
        assert "Do the steps." in tool("demo")


def test_unknown_name_lists_available(tmp_path):
    tool = make_load_skill_tool([make_skill(tmp_path, "a"), make_skill(tmp_path, "b")], role="coder", notice=None)
    assert tool("zzz") == "Unknown skill zzz. Available: a, b"


def test_notice_once_per_skill(tmp_path):
    notices: list[str] = []
    skill = make_skill(tmp_path)
    (skill.path / "ref.md").write_text("x", encoding="utf-8")
    tool = make_load_skill_tool([skill], role="coder", notice=notices.append)
    tool("demo")
    tool("demo", "ref.md")
    assert notices == ["Using skill demo (coder)"]


def test_refused_load_gives_no_notice(tmp_path):
    notices: list[str] = []
    tool = make_load_skill_tool([make_skill(tmp_path)], role="coder", notice=notices.append)
    tool("demo", "../x")
    tool("nope")
    assert notices == []
