import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from phil.agents.registry import SPECS
from phil.agents.spec import load_prompt
import phil.memory.context as context_module
from phil.memory.context import (
    AGENT_FILE_MAX_BYTES,
    TRUNCATED_NOTE,
    build_repo_context,
    read_agent_file,
    read_run_context,
    write_run_context,
)
from phil.memory.doc import MAX_FILE_BYTES, Bullet
from phil.memory.store import update_memory
from phil.store.paths import ProjectPaths


def test_agents_md_is_preferred_over_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("claude\n", encoding="utf-8")
    assert read_agent_file(tmp_path) == ("CLAUDE.md", "claude\n")
    (tmp_path / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    assert read_agent_file(tmp_path) == ("AGENTS.md", "agents\n")


def test_a_directory_named_agents_md_is_not_an_agent_file(tmp_path):
    (tmp_path / "AGENTS.md").mkdir()
    assert read_agent_file(tmp_path) == ("", "")
    assert build_repo_context(tmp_path, ProjectPaths("x", home=tmp_path / "home")).problems == [
        "skipped AGENTS.md: not a regular file"
    ]


def test_run_context_is_written_atomically(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("Run `make test`.\n", encoding="utf-8")
    paths = ProjectPaths("x", home=tmp_path / "home")
    run_dir = tmp_path / "run"
    calls = []
    real = context_module.write_atomic
    monkeypatch.setattr(context_module, "write_atomic", lambda path, data, **kw: (calls.append(path), real(path, data, **kw)))
    write_run_context(run_dir, tmp_path, paths)
    assert calls == [run_dir / "context.md"]
    assert "Run `make test`." in read_run_context(run_dir)
    assert not list(run_dir.glob("*.tmp"))


def test_run_context_is_written_and_read_back(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Run `make test`.\n", encoding="utf-8")
    paths = ProjectPaths("x", home=tmp_path / "home")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert write_run_context(run_dir, tmp_path, paths) == []
    assert read_run_context(run_dir) == build_repo_context(tmp_path, paths).text
    assert "Run `make test`." in read_run_context(run_dir)


def test_run_context_is_an_empty_file_when_there_is_no_block(tmp_path):
    paths = ProjectPaths("x", home=tmp_path / "home")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_run_context(run_dir, tmp_path, paths)
    assert (run_dir / "context.md").read_text(encoding="utf-8") == ""
    assert read_run_context(run_dir) == ""


def test_read_run_context_is_empty_for_a_missing_or_unreadable_file(tmp_path):
    assert read_run_context(tmp_path) == ""
    (tmp_path / "context.md").mkdir()  # reading a directory fails
    assert read_run_context(tmp_path) == ""


def test_no_files_no_block(tmp_path):
    assert build_repo_context(tmp_path, ProjectPaths("x", home=tmp_path / "home")).text == ""


def test_block_order_and_marks(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Run `make test`.\n", encoding="utf-8")
    paths = ProjectPaths("x", home=tmp_path / "home")
    update_memory(paths, lambda d: d.add(Bullet("Gotchas", "Set PHIL_HOME", True)))
    text = build_repo_context(tmp_path, paths).text
    assert text.startswith("## Repo context\nThese notes describe this repository.")
    assert text.index("### From AGENTS.md\nRun `make test`.") < text.index("### From Phil's memory\n## Gotchas\n- Set PHIL_HOME")
    assert "<!--" not in text


def test_big_invalid_utf8_claude_md_is_cut_with_a_note(tmp_path):
    line = b"\xff\xfe bad bytes and a long line of text\n"
    (tmp_path / "CLAUDE.md").write_bytes(line * (AGENT_FILE_MAX_BYTES // len(line) + 50))
    built = build_repo_context(tmp_path, ProjectPaths("x", home=tmp_path / "home"))
    assert "### From CLAUDE.md" in built.text and built.text.rstrip().endswith(TRUNCATED_NOTE)
    assert built.problems == []


def test_big_memory_text_is_cut_with_a_note(tmp_path):
    paths = ProjectPaths("x", home=tmp_path / "home")
    paths.memory_path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"- note number {i} with some padding to fill the file up\n" for i in range(400))
    paths.memory_path.write_text("## Gotchas\n" + body, encoding="utf-8", newline="\n")
    text = build_repo_context(tmp_path, paths).text
    assert text.rstrip().endswith(TRUNCATED_NOTE)
    memory_part = text.split("### From Phil's memory\n", 1)[1]
    assert len(memory_part.encode("utf-8")) <= MAX_FILE_BYTES + len(TRUNCATED_NOTE) + 2


def test_an_unreadable_agent_file_is_a_problem_not_an_error(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("x\n", encoding="utf-8")
    real = os.open

    def boom(file, *args, **kwargs):
        if Path(file).name == "AGENTS.md":
            raise PermissionError("denied")
        return real(file, *args, **kwargs)

    monkeypatch.setattr(os, "open", boom)
    built = build_repo_context(tmp_path, ProjectPaths("x", home=tmp_path / "home"))
    assert built.problems == ["couldn't read AGENTS.md: PermissionError: denied"]
    assert built.text == ""


def test_skipped_problems_survive_a_later_read_error(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET\n", encoding="utf-8")
    symlink(repo / "AGENTS.md", secret)
    (repo / "CLAUDE.md").write_text("claude notes\n", encoding="utf-8")
    real = os.open

    def boom(file, *args, **kwargs):
        if Path(file).name == "CLAUDE.md":
            raise PermissionError("denied")
        return real(file, *args, **kwargs)

    monkeypatch.setattr(os, "open", boom)
    built = build_repo_context(repo, ProjectPaths("x", home=tmp_path / "home"))
    assert built.problems == [
        "skipped AGENTS.md: it points outside the repository",
        "couldn't read CLAUDE.md: PermissionError: denied",
    ]


def test_a_hard_linked_agent_file_is_refused_and_the_next_name_used(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET\n", encoding="utf-8")
    try:
        os.link(secret, repo / "AGENTS.md")
    except (OSError, NotImplementedError):
        pytest.skip("hard links can't be created here")
    (repo / "CLAUDE.md").write_text("claude notes\n", encoding="utf-8")
    built = build_repo_context(repo, ProjectPaths("x", home=tmp_path / "home"))
    assert "TOP SECRET" not in built.text and "### From CLAUDE.md\nclaude notes" in built.text
    assert built.problems == ["skipped AGENTS.md: it has other hard links"]


def test_an_opened_file_that_is_not_the_one_checked_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("x\n", encoding="utf-8")
    real = os.fstat

    def other_inode(fd):
        info = real(fd)
        return SimpleNamespace(
            st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino + 1, st_nlink=info.st_nlink
        )

    monkeypatch.setattr(os, "fstat", other_inode)
    built = build_repo_context(tmp_path, ProjectPaths("x", home=tmp_path / "home"))
    assert built.problems == ["skipped AGENTS.md: it changed while being read"]
    assert built.text == ""


def symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks can't be created here")


def test_a_link_inside_the_repo_is_accepted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("inside\n", encoding="utf-8")
    symlink(repo / "CLAUDE.md", repo / "docs" / "a.md")
    built = build_repo_context(repo, ProjectPaths("x", home=tmp_path / "home"))
    assert "### From CLAUDE.md\ninside" in built.text and built.problems == []


def test_a_link_outside_the_repo_is_skipped_and_the_next_name_is_tried(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET\n", encoding="utf-8")
    symlink(repo / "AGENTS.md", secret)
    (repo / "CLAUDE.md").write_text("claude notes\n", encoding="utf-8")
    built = build_repo_context(repo, ProjectPaths("x", home=tmp_path / "home"))
    assert "TOP SECRET" not in built.text and "### From CLAUDE.md\nclaude notes" in built.text
    assert built.problems == ["skipped AGENTS.md: it points outside the repository"]


def test_a_link_into_git_is_skipped_and_the_next_name_is_tried(tmp_path):
    # .git/config can hold a remote URL with a token; a cloned repo must not pull it into prompts.
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text("url = https://TOKEN@github.com/x/y\n", encoding="utf-8")
    symlink(repo / "AGENTS.md", repo / ".git" / "config")
    (repo / "CLAUDE.md").write_text("claude notes\n", encoding="utf-8")
    built = build_repo_context(repo, ProjectPaths("x", home=tmp_path / "home"))
    assert "TOKEN" not in built.text and "### From CLAUDE.md\nclaude notes" in built.text
    assert built.problems == ["skipped AGENTS.md: it points into .git"]


@pytest.mark.parametrize(
    ("relative", "inside"),
    [
        (".git/config", True),
        (".GIT/config", True),
        (".git./config", True),
        (".git", True),
        (".github/AGENTS.md", False),
        ("docs/.git/notes.md", False),
    ],
)
def test_inside_git_dir(relative, inside):
    assert context_module._inside_git_dir(Path(relative)) is inside


def test_a_link_into_github_is_accepted(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".github").mkdir(parents=True)
    (repo / ".github" / "AGENTS.md").write_text("from github dir\n", encoding="utf-8")
    symlink(repo / "AGENTS.md", repo / ".github" / "AGENTS.md")
    built = build_repo_context(repo, ProjectPaths("x", home=tmp_path / "home"))
    assert "### From AGENTS.md\nfrom github dir" in built.text and built.problems == []


def test_a_dangling_link_is_skipped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    symlink(repo / "AGENTS.md", repo / "missing.md")
    built = build_repo_context(repo, ProjectPaths("x", home=tmp_path / "home"))
    assert built.text == "" and built.problems == ["skipped AGENTS.md: it can't be resolved"]


def test_a_directory_is_reported_as_not_a_regular_file(tmp_path):
    (tmp_path / "AGENTS.md").mkdir()
    built = build_repo_context(tmp_path, ProjectPaths("x", home=tmp_path / "home"))
    assert built.problems == ["skipped AGENTS.md: not a regular file"]


def test_a_huge_agent_file_is_read_with_a_bound(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_bytes(b"line of text\n" * (1024 * 1024 // 13))
    sizes = []
    real = os.fdopen

    class Spy:
        def __init__(self, handle):
            self.handle = handle

        def read(self, n=-1):
            sizes.append(n)
            return self.handle.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

    monkeypatch.setattr(os, "fdopen", lambda fd, *args, **kwargs: Spy(real(fd, *args, **kwargs)))
    text = build_repo_context(tmp_path, ProjectPaths("x", home=tmp_path / "home")).text
    assert sizes == [AGENT_FILE_MAX_BYTES + 1]
    assert text.rstrip().endswith(TRUNCATED_NOTE)


def test_line_endings_are_normalised(tmp_path):
    (tmp_path / "AGENTS.md").write_bytes(b"one\r\ntwo\rthree\n")
    text = build_repo_context(tmp_path, ProjectPaths("x", home=tmp_path / "home")).text
    assert "\r" not in text and "one\ntwo\nthree" in text


def test_load_prompt_is_unchanged_without_context_and_appends_it_with():
    spec = SPECS["implementer"]
    plain = load_prompt(spec)
    assert load_prompt(replace(spec, context="")) == plain
    assert load_prompt(replace(spec, context="## Repo context\nx")) == plain.rstrip("\n") + "\n\n## Repo context\nx\n"


def test_which_roles_get_the_block():
    with_block = {name for name, spec in SPECS.items() if spec.repo_context}
    assert with_block == {"intake", "architect", "critic", "implementer", "tester", "reviewer", "btw",
                          "answer", "quick_implementer", "design"}
