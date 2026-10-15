import pytest

from phil.memory.doc import Bullet
from phil.memory.store import read_memory, update_memory
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


def test_an_unchanged_doc_is_not_rewritten(paths):
    paths.project_dir.mkdir(parents=True)
    paths.memory_path.write_text("free prose\n", encoding="utf-8")
    before = paths.memory_path.stat().st_mtime_ns
    update_memory(paths, lambda doc: None)
    assert paths.memory_path.stat().st_mtime_ns == before
