import hashlib
import os

import pytest

from phil.skills.discover import Skill
from phil.skills.tool import (
    MAX_FILE_BYTES,
    MAX_LOADS,
    SKILL_CHANGED,
    SkillReadError,
    make_load_skill_tool,
    read_skill_file,
)


def make_skill(root, name="demo", body="Do the steps.\n") -> Skill:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8")
    digest = hashlib.sha256((directory / "SKILL.md").read_bytes()).hexdigest()
    return Skill(name, "d", directory.resolve(), "agents", digest)


def test_a_changed_skill_md_is_refused(tmp_path):
    skill = make_skill(tmp_path, body="Do the steps.\n")
    (skill.path / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nDo something else.\n", encoding="utf-8")
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    assert tool("demo") == f"Can't read SKILL.md from demo: {SKILL_CHANGED}."
    with pytest.raises(SkillReadError, match=SKILL_CHANGED):
        read_skill_file(skill)


def test_a_large_skill_md_is_hashed_whole(tmp_path):
    body = "a" * (MAX_FILE_BYTES * 2)
    skill = make_skill(tmp_path, body=body)
    assert "[truncated: " in read_skill_file(skill)  # unchanged: read, cut to the limit
    (skill.path / "SKILL.md").write_text(f"---\nname: demo\ndescription: d\n---\n{body[:-1]}b", encoding="utf-8")
    with pytest.raises(SkillReadError, match=SKILL_CHANGED):  # only a byte past the limit changed
        read_skill_file(skill)


def test_hashing_a_large_skill_md_reads_in_bounded_chunks(tmp_path, monkeypatch):
    skill = make_skill(tmp_path, body="a" * (MAX_FILE_BYTES * 3))
    sizes: list[int] = []
    real_read = os.read

    def recording_read(fd, size):
        sizes.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(os, "read", recording_read)
    read_skill_file(skill)
    assert max(sizes) <= MAX_FILE_BYTES + 1


def test_a_change_during_the_read_is_discarded(tmp_path):
    skill = make_skill(tmp_path)
    answers = iter([True, False, True, True, True, True, True, True])  # approved before the read, not after
    tool = make_load_skill_tool([skill], role="coder", notice=None, still_approved=lambda _: next(answers))
    result = tool("demo")
    assert result == f"Can't read SKILL.md from demo: {SKILL_CHANGED}."
    assert "Do the steps." not in result
    for _ in range(MAX_LOADS):  # the discarded read did not count
        assert "Do the steps." in tool("demo")


def test_a_refused_approval_reads_nothing_and_does_not_count(tmp_path):
    skill = make_skill(tmp_path)
    approved = [False]
    tool = make_load_skill_tool([skill], role="coder", notice=None, still_approved=lambda _: approved[0])
    assert tool("demo") == f"Can't read SKILL.md from demo: {SKILL_CHANGED}."
    assert tool("demo", "ref.md") == f"Can't read ref.md from demo: {SKILL_CHANGED}."
    approved[0] = True
    for _ in range(MAX_LOADS):
        assert "Do the steps." in tool("demo")


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


def test_a_hard_link_is_refused(tmp_path):
    skill = make_skill(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    os.link(secret, skill.path / "hard.md")
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    result = tool("demo", "hard.md")
    assert result == "Can't read hard.md from demo: it is a hard link."
    assert "TOP SECRET" not in result


def test_a_nul_byte_is_refused(tmp_path):
    tool = make_load_skill_tool([make_skill(tmp_path)], role="coder", notice=None)
    assert tool("demo", "a\x00b").startswith("Can't read ")
    assert tool("demo", "SKILL.md\x00") .startswith("Can't read ")


@pytest.mark.parametrize("requested", ["\\\\host\\share\\x", "//host/share/x", "C:foo", "C:\\x", "/etc/passwd", "..", ""])
def test_an_anchored_or_unc_path_is_refused(tmp_path, monkeypatch, requested):
    skill = make_skill(tmp_path)
    tool = make_load_skill_tool([skill], role="coder", notice=None)

    def forbidden(self, *args, **kwargs):
        raise AssertionError("the filesystem was touched before the path was checked")

    monkeypatch.setattr("pathlib.Path.resolve", forbidden)
    assert tool("demo", requested) == f"Can't read {requested} from demo: it is outside the skill's folder."


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_fifo_is_refused(tmp_path):
    skill = make_skill(tmp_path)
    os.mkfifo(skill.path / "pipe")
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    assert tool("demo", "pipe").startswith("Can't read pipe from demo: ")


def test_outside_paths_get_one_message(tmp_path):
    skill = make_skill(tmp_path)
    (tmp_path / "exists.txt").write_text("x", encoding="utf-8")
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    existing = tool("demo", "../exists.txt").replace("../exists.txt", "F")
    missing = tool("demo", "../missing.txt").replace("../missing.txt", "F")
    assert existing == missing
    assert tool("demo", str(tmp_path / "exists.txt")).endswith("it is outside the skill's folder.")
    assert tool("demo", str(tmp_path / "missing.txt")).endswith("it is outside the skill's folder.")


def test_at_most_the_limit_plus_one_byte_is_read(tmp_path, monkeypatch):
    skill = make_skill(tmp_path)
    (skill.path / "big.txt").write_text("a" * (MAX_FILE_BYTES * 3), encoding="utf-8")
    sizes: list[int] = []
    real_read = os.read

    def recording_read(fd, size):
        sizes.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(os, "read", recording_read)
    tool = make_load_skill_tool([skill], role="coder", notice=None)
    assert "[truncated: " in tool("demo", "big.txt")
    assert sizes and sum(sizes) <= MAX_FILE_BYTES + 1 and max(sizes) <= MAX_FILE_BYTES + 1


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
