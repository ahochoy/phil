"""/memory, /remember and /forget in the chat (spec 2026-10-15 §2.4): the CLI's wording, as plain text."""

from phil.memory.agents_md import add_suggestions, read_suggestions
from phil.memory.doc import Bullet
from phil.memory.listing import memory_lines
from phil.memory.store import read_memory, update_memory
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run, update_run
from tests.chat.test_controller import run_chat


def paths_of(repo):
    return ProjectPaths(resolve_repo(repo).slug)


def texts(repo):
    return [bullet.text for bullet in read_memory(paths_of(repo)).bullets()]


def suggest(repo, *lines):
    add_suggestions(paths_of(repo), list(lines))


def test_memory_with_nothing_remembered(calc_repo):
    text, *_ = run_chat(calc_repo, ["/memory"], {})
    for line in memory_lines(paths_of(calc_repo)):
        assert line in text
    assert "No memory yet for this repo." in text


def test_remember_with_a_section_then_memory_lists_it(calc_repo):
    text, *_ = run_chat(calc_repo, ["/remember Commands: run make", "/memory"], {})
    assert "Remembered under Commands." in text
    assert "1. run make  (you)" in text
    assert texts(calc_repo) == ["run make"]


def test_remember_without_a_section_uses_conventions_and_ignores_other_colons(calc_repo):
    text, *_ = run_chat(calc_repo, ["/remember use http://x for docs", "/remember gotchas: slow tests"], {})
    assert text.count("Remembered under Conventions.") == 1
    assert "Remembered under Gotchas." in text
    sections = {bullet.text: bullet.section for bullet in read_memory(paths_of(calc_repo)).bullets()}
    assert sections == {"use http://x for docs": "Conventions", "slow tests": "Gotchas"}


def test_remember_with_nothing_after_it_prints_usage(calc_repo):
    text, *_ = run_chat(calc_repo, ["/remember", "/remember Commands:"], {})
    assert text.count("Usage: /remember [section:] text") == 2
    assert texts(calc_repo) == []


def test_remember_refuses_a_secret(calc_repo):
    text, *_ = run_chat(calc_repo, ["/remember token is sk-abcdefghijklmnopqrstuvwxyz123456"], {})
    assert "Not added:" in text
    assert "Remembered" not in text
    assert texts(calc_repo) == []


def test_memory_text_is_not_markup(calc_repo):
    run_chat(calc_repo, ["/remember keep [bold]this[/bold] as written"], {})
    text, *_ = run_chat(calc_repo, ["/memory"], {})
    assert "keep [bold]this[/bold] as written" in text


def test_forget_removes_a_bullet(calc_repo):
    update_memory(paths_of(calc_repo), lambda doc: doc.add(Bullet("Commands", "run make", False)))
    text, *_ = run_chat(calc_repo, ["/forget 1", "/forget 1", "/forget x"], {})
    assert "Forgot: run make" in text
    assert "No bullet 1. /memory lists them." in text
    assert "Usage: /forget <n>" in text
    assert texts(calc_repo) == []


def test_apply_then_yes_yes_creates_agents_md(calc_repo):
    suggest(calc_repo, "Run make before pushing")
    text, *_ = run_chat(calc_repo, ["/memory apply", "y", "y", "/memory suggestions"], {})
    assert "1. Run make before pushing" in text
    assert "Append these to AGENTS.md? (y/n)" in text
    assert "There's no AGENTS.md. Create it? (y/n)" in text
    assert f"Added 1 line(s) to {calc_repo / 'AGENTS.md'}. Not committed: review and commit it yourself." in text
    assert "Run make before pushing" in (calc_repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "No suggestions for AGENTS.md." in text


def test_apply_with_an_existing_agents_md_asks_once(calc_repo):
    (calc_repo / "AGENTS.md").write_text("# Notes\n", encoding="utf-8", newline="\n")
    suggest(calc_repo, "Run make before pushing")
    text, *_ = run_chat(calc_repo, ["/memory apply", "y"], {})
    assert "Create it?" not in text
    assert "## Notes from Phil" in (calc_repo / "AGENTS.md").read_text(encoding="utf-8")


def test_apply_then_no_writes_nothing(calc_repo):
    suggest(calc_repo, "Run make before pushing")
    text, *_ = run_chat(calc_repo, ["/memory apply", "n"], {})
    assert "Left AGENTS.md as it is." in text
    assert not (calc_repo / "AGENTS.md").exists()
    assert read_suggestions(paths_of(calc_repo)) == ["Run make before pushing"]


def test_declining_to_create_agents_md_writes_nothing(calc_repo):
    suggest(calc_repo, "Run make before pushing")
    text, *_ = run_chat(calc_repo, ["/memory apply", "y", "no"], {})
    assert "Left AGENTS.md as it is." in text
    assert not (calc_repo / "AGENTS.md").exists()


def test_apply_returns_to_idle_so_the_next_line_is_a_command(calc_repo):
    suggest(calc_repo, "Run make before pushing")
    text, *_ = run_chat(calc_repo, ["/memory apply", "n", "/runs"], {})
    assert "No runs yet." in text


def test_ctrl_c_leaves_agents_md_as_it_is(calc_repo):
    suggest(calc_repo, "Run make before pushing")

    def interrupt(controller):
        controller._interrupt()
        return "/runs"

    text, *_ = run_chat(calc_repo, ["/memory apply", interrupt], {})
    assert "Left AGENTS.md as it is." in text
    assert "No runs yet." in text
    assert not (calc_repo / "AGENTS.md").exists()


def test_apply_with_nothing_pending(calc_repo):
    text, *_ = run_chat(calc_repo, ["/memory apply"], {})
    assert "No suggestions for AGENTS.md." in text


def test_apply_is_refused_while_a_run_is_in_progress(calc_repo, tmp_path):
    suggest(calc_repo, "Run make before pushing")
    conn = connect(paths_of(calc_repo).db_path)
    try:
        create_run(conn, run_id="r-0001", keyword="a", base_sha="x", worktree=tmp_path / "w", tasks_total=1)
        update_run(conn, "r-0001", state="running")
    finally:
        conn.close()
    text, *_ = run_chat(calc_repo, ["/memory apply"], {})
    assert "Run r-0001 is in progress; apply suggestions when it finishes." in text
    assert "Append these" not in text


def test_apply_is_refused_while_this_chats_run_has_not_ended(calc_repo):
    suggest(calc_repo, "Run make before pushing")

    def follow(controller):
        controller._run_id = "r-0042"
        return "/memory apply"

    text, *_ = run_chat(calc_repo, [follow], {})
    assert "Run r-0042 is in progress; apply suggestions when it finishes." in text
    assert "Append these" not in text
    assert not (calc_repo / "AGENTS.md").exists()


def test_dismiss_throws_the_suggestions_away(calc_repo):
    suggest(calc_repo, "one", "two")
    text, *_ = run_chat(calc_repo, ["/memory suggestions", "/memory dismiss"], {})
    assert "1. one" in text and "2. two" in text
    assert "Dismissed 2 suggestion(s)." in text
    assert read_suggestions(paths_of(calc_repo)) == []


def test_edit_creates_the_file_opens_the_editor_and_lists(calc_repo, monkeypatch):
    opened = []
    monkeypatch.setattr("phil.chat.controller.edit_file", lambda path: opened.append(path))
    text, *_ = run_chat(calc_repo, ["/memory edit"], {})
    memory_path = paths_of(calc_repo).memory_path
    assert opened == [str(memory_path)]
    assert memory_path.exists()
    assert "No memory yet for this repo." in text


def test_edit_reports_an_editor_that_wont_open(calc_repo, monkeypatch):
    from phil.memory.editor import EditorError

    def broken(path):
        raise EditorError("no editor")

    monkeypatch.setattr("phil.chat.controller.edit_file", broken)
    text, *_ = run_chat(calc_repo, ["/memory edit"], {})
    assert "Couldn't open the editor: no editor" in text


def test_an_unknown_subcommand_lists_the_choices(calc_repo):
    text, *_ = run_chat(calc_repo, ["/memory xyz"], {})
    assert "Try /memory, /memory edit, /memory suggestions, /memory apply or /memory dismiss." in text


def test_help_lists_the_memory_commands(calc_repo):
    text, *_ = run_chat(calc_repo, ["/help"], {})
    flat = " ".join(text.split())
    assert "/memory (what Phil remembers about this repo), /remember <fact>, /forget <n>," in flat
