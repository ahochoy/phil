import os

import pytest

from phil.memory.doc import Bullet
from phil.memory.store import MemoryWriteError, read_memory, update_memory, write_atomic
from phil.store.paths import ProjectPaths


@pytest.fixture
def paths(tmp_path):
    return ProjectPaths("calc-1", home=tmp_path)


def test_missing_file_reads_as_the_template(paths):
    assert read_memory(paths).render().startswith("# Phil memory — calc-1\n")
    assert not paths.memory_path.exists()


def test_update_writes_atomically_and_returns_the_result(paths):
    added = update_memory(paths, lambda doc: doc.add(Bullet("Commands", "Tests: `pytest`", True)) or 1)
    assert added == 1
    assert "- Tests: `pytest` <!-- phil -->\n" in paths.memory_path.read_text(encoding="utf-8")
    assert not list(paths.project_dir.glob("*.tmp"))


def test_update_applies_to_the_text_on_disk_at_write_time(paths):
    paths.project_dir.mkdir(parents=True)
    paths.memory_path.write_text("# mine\n\n## Gotchas\n- a user note\n", encoding="utf-8")
    stale = read_memory(paths)  # read before the user's edit
    paths.memory_path.write_text("# mine\n\n## Gotchas\n- a user note\n- edited later\n", encoding="utf-8")
    update_memory(paths, lambda doc: doc.add(Bullet("Gotchas", "from phil", True)))
    text = paths.memory_path.read_text(encoding="utf-8")
    assert "- edited later\n- from phil <!-- phil -->\n" in text
    assert stale.render() != text


def test_invalid_utf8_is_refused_and_the_bytes_are_left_unchanged(paths):
    paths.project_dir.mkdir(parents=True)
    original = b"# mine\n\n## Gotchas\n- bad \xff byte\n"
    paths.memory_path.write_bytes(original)
    with pytest.raises(MemoryWriteError, match="memory.md isn't valid UTF-8; not updated"):
        update_memory(paths, lambda doc: doc.add(Bullet("Gotchas", "from phil", True)))
    assert paths.memory_path.read_bytes() == original
    assert "bad" in read_memory(paths).render()  # reading for prompts stays lenient


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_a_new_memory_file_is_private(paths):
    update_memory(paths, lambda doc: doc.add(Bullet("Commands", "Tests: `pytest`", True)))
    assert paths.memory_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_write_atomic_keeps_an_existing_mode_and_uses_new_mode_otherwise(tmp_path):
    existing = tmp_path / "existing"
    existing.write_bytes(b"x")
    os.chmod(existing, 0o640)
    write_atomic(existing, b"y")
    assert existing.stat().st_mode & 0o777 == 0o640
    write_atomic(tmp_path / "fresh", b"z")
    assert (tmp_path / "fresh").stat().st_mode & 0o777 == 0o600
    write_atomic(tmp_path / "shared", b"z", new_mode=0o644)
    assert (tmp_path / "shared").stat().st_mode & 0o777 == 0o644


def test_an_unchanged_doc_is_not_rewritten(paths):
    paths.project_dir.mkdir(parents=True)
    paths.memory_path.write_text("free prose\n", encoding="utf-8")
    before = paths.memory_path.stat().st_mtime_ns
    update_memory(paths, lambda doc: None)
    assert paths.memory_path.stat().st_mtime_ns == before
