import os

import pytest

from phil.memory.agents_md import (
    NOTES_HEADING,
    active_run,
    add_suggestions,
    apply_suggestions,
    clear_suggestions,
    read_suggestions,
)
from phil.memory.doc import Bullet
from phil.memory.store import MemoryWriteError, read_memory, update_memory
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run, update_run


@pytest.fixture
def paths(tmp_path):
    return ProjectPaths("calc-1", home=tmp_path / "home")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


def test_appends_under_an_existing_heading(repo, paths):
    (repo / "AGENTS.md").write_bytes(f"# Repo\n\n{NOTES_HEADING}\n- old note\n".encode())
    add_suggestions(paths, ["Run `make lint`", "Use tabs"])
    result = apply_suggestions(repo, paths, create=False)
    assert (repo / "AGENTS.md").read_bytes() == (
        f"# Repo\n\n{NOTES_HEADING}\n- old note\n- Run `make lint`\n- Use tabs\n".encode()
    )
    assert result.lines == ["Run `make lint`", "Use tabs"]
    assert result.created is False
    assert result.path == repo / "AGENTS.md"


def test_adds_the_heading_when_missing_and_keeps_other_bytes(repo, paths):
    original = b"# Repo\nbad \xff byte\nno trailing newline"
    (repo / "AGENTS.md").write_bytes(original)
    add_suggestions(paths, ["Run `make lint`"])
    apply_suggestions(repo, paths, create=False)
    expected = original + f"\n\n{NOTES_HEADING}\n- Run `make lint`\n".encode()
    assert (repo / "AGENTS.md").read_bytes() == expected


def test_heading_added_after_one_blank_line_when_file_ends_with_newline(repo, paths):
    (repo / "AGENTS.md").write_bytes(b"# Repo\n")
    add_suggestions(paths, ["a note"])
    apply_suggestions(repo, paths, create=False)
    assert (repo / "AGENTS.md").read_bytes() == f"# Repo\n\n{NOTES_HEADING}\n- a note\n".encode()


def test_no_file_without_create_raises_and_keeps_suggestions(repo, paths):
    add_suggestions(paths, ["a note"])
    with pytest.raises(FileNotFoundError):
        apply_suggestions(repo, paths, create=False)
    assert read_suggestions(paths) == ["a note"]
    assert not (repo / "AGENTS.md").exists()


def test_create_makes_the_file(repo, paths):
    add_suggestions(paths, ["a note"])
    result = apply_suggestions(repo, paths, create=True)
    assert result.created is True
    assert (repo / "AGENTS.md").read_bytes() == f"{NOTES_HEADING}\n- a note\n".encode()


def test_nothing_pending_raises(repo, paths):
    (repo / "AGENTS.md").write_bytes(b"# Repo\n")
    with pytest.raises(ValueError, match="no suggestions"):
        apply_suggestions(repo, paths, create=False)
    assert (repo / "AGENTS.md").read_bytes() == b"# Repo\n"


def test_duplicate_phil_bullets_leave_memory_but_user_bullets_stay(repo, paths):
    update_memory(paths, lambda doc: doc.add(Bullet("Commands", "Run  `Make lint`", True)))
    update_memory(paths, lambda doc: doc.add(Bullet("Commands", "run `make lint`", False)))
    update_memory(paths, lambda doc: doc.add(Bullet("Gotchas", "unrelated", True)))
    add_suggestions(paths, ["Run `make lint`"])
    result = apply_suggestions(repo, paths, create=True)
    assert result.removed_from_memory == 1
    remaining = [(b.text, b.by_phil) for b in read_memory(paths).bullets()]
    assert remaining == [("run `make lint`", False), ("unrelated", True)]


def test_suggestions_are_cleared_after_applying(repo, paths):
    add_suggestions(paths, ["a note"])
    apply_suggestions(repo, paths, create=True)
    assert read_suggestions(paths) == []
    assert not list(paths.project_dir.glob("*.tmp"))
    assert not list(repo.glob("*.tmp"))


def test_lines_failing_the_check_are_skipped_and_dropped(repo, paths):
    paths.project_dir.mkdir(parents=True)
    paths.agents_md_suggestions_path.write_text("- fine\n- has <!-- comment\n- " + "x" * 300 + "\n", encoding="utf-8")
    result = apply_suggestions(repo, paths, create=True)
    assert result.lines == ["fine"]
    assert (repo / "AGENTS.md").read_bytes() == f"{NOTES_HEADING}\n- fine\n".encode()
    assert read_suggestions(paths) == []


def test_every_line_failing_raises_and_still_clears(repo, paths):
    paths.project_dir.mkdir(parents=True)
    paths.agents_md_suggestions_path.write_text("- has <!-- comment\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no suggestions"):
        apply_suggestions(repo, paths, create=True)
    assert read_suggestions(paths) == []
    assert not (repo / "AGENTS.md").exists()


def test_a_directory_is_not_a_regular_file(repo, paths):
    (repo / "AGENTS.md").mkdir()
    add_suggestions(paths, ["a note"])
    with pytest.raises(ValueError, match="not a regular file"):
        apply_suggestions(repo, paths, create=True)
    assert read_suggestions(paths) == ["a note"]


def test_a_symlink_to_outside_the_repo_is_refused(repo, paths, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"secret\n")
    try:
        os.symlink(outside, repo / "AGENTS.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    add_suggestions(paths, ["a note"])
    with pytest.raises(ValueError, match="is a symbolic link"):
        apply_suggestions(repo, paths, create=True)
    assert outside.read_bytes() == b"secret\n"


def test_a_planted_temp_symlink_is_not_written_through(repo, paths, tmp_path):
    outside = tmp_path / "bashrc"
    outside.write_bytes(b"keep me\n")
    try:
        os.symlink(outside, repo / "AGENTS.md.phil.tmp")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    (repo / "AGENTS.md").write_bytes(b"# Repo\n")
    add_suggestions(paths, ["a note"])
    apply_suggestions(repo, paths, create=False)
    assert outside.read_bytes() == b"keep me\n"
    assert (repo / "AGENTS.md").read_bytes() == f"# Repo\n\n{NOTES_HEADING}\n- a note\n".encode()
    assert sorted(p.name for p in repo.iterdir()) == ["AGENTS.md", "AGENTS.md.phil.tmp"]


def test_a_memory_failure_is_reported_and_the_notes_are_still_written(repo, paths, monkeypatch):
    def boom(*args, **kwargs):
        raise MemoryWriteError("disk full")

    monkeypatch.setattr("phil.memory.agents_md.update_memory", boom)
    add_suggestions(paths, ["a note"])
    result = apply_suggestions(repo, paths, create=True)
    assert result.memory_error == "MemoryWriteError: disk full"
    assert result.removed_from_memory == 0
    assert (repo / "AGENTS.md").read_bytes() == f"{NOTES_HEADING}\n- a note\n".encode()
    assert read_suggestions(paths) == []


def test_no_memory_error_normally(repo, paths):
    add_suggestions(paths, ["a note"])
    assert apply_suggestions(repo, paths, create=True).memory_error is None


_LINK_REFUSED = "AGENTS.md is a symbolic link; Phil only appends to a regular AGENTS.md file"


@pytest.mark.parametrize("target", ["docs/AGENTS.md", ".git/config"])
def test_an_in_repo_symlink_is_refused_and_its_target_unchanged(repo, paths, target):
    (repo / target).parent.mkdir(parents=True)
    (repo / target).write_bytes(b"# keep me\n")
    try:
        os.symlink(repo / target, repo / "AGENTS.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    add_suggestions(paths, ["a note"])
    with pytest.raises(ValueError, match=_LINK_REFUSED):
        apply_suggestions(repo, paths, create=True)
    assert (repo / target).read_bytes() == b"# keep me\n"
    assert (repo / "AGENTS.md").is_symlink()
    assert read_suggestions(paths) == ["a note"]


def test_a_hard_linked_file_is_refused(repo, paths, tmp_path):
    (repo / "other.md").write_bytes(b"x\n")
    try:
        os.link(repo / "other.md", repo / "AGENTS.md")
    except (OSError, NotImplementedError):
        pytest.skip("hard links unavailable")
    add_suggestions(paths, ["a note"])
    with pytest.raises(ValueError, match="not a regular file"):
        apply_suggestions(repo, paths, create=False)
    assert (repo / "other.md").read_bytes() == b"x\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
@pytest.mark.parametrize("umask", [0o022, 0o077])
def test_a_new_agents_md_honours_the_umask(repo, paths, umask):
    add_suggestions(paths, ["a note"])
    old = os.umask(umask)
    try:
        apply_suggestions(repo, paths, create=True)
    finally:
        os.umask(old)
    assert (repo / "AGENTS.md").stat().st_mode & 0o777 == 0o666 & ~umask


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_the_suggestions_file_is_private(paths):
    add_suggestions(paths, ["a note"])
    assert paths.agents_md_suggestions_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_the_file_mode_is_kept(repo, paths):
    (repo / "AGENTS.md").write_bytes(b"# Repo\n")
    os.chmod(repo / "AGENTS.md", 0o664)
    add_suggestions(paths, ["a note"])
    apply_suggestions(repo, paths, create=False)
    assert (repo / "AGENTS.md").stat().st_mode & 0o777 == 0o664


def test_clear_suggestions_returns_the_count(paths):
    assert clear_suggestions(paths) == 0
    add_suggestions(paths, ["one", "two"])
    assert clear_suggestions(paths) == 2
    assert read_suggestions(paths) == []


def test_active_run_finds_a_running_run_and_ignores_a_completed_one(tmp_path):
    conn = connect(tmp_path / "db" / "phil.db")
    try:
        assert active_run(conn) is None
        create_run(conn, run_id="r-0001", keyword="a", base_sha="x", worktree=tmp_path / "w1", tasks_total=1)
        update_run(conn, "r-0001", state="running")
        update_run(conn, "r-0001", state="completed")
        assert active_run(conn) is None
        create_run(conn, run_id="r-0002", keyword="b", base_sha="x", worktree=tmp_path / "w2", tasks_total=1)
        update_run(conn, "r-0002", state="running")
        assert active_run(conn) == "r-0002"
    finally:
        conn.close()
