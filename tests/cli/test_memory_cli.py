"""`phil memory` (spec 2026-10-15 §2.4): show, add, remove, edit, clear, and apply or dismiss the
suggestions for AGENTS.md."""

from typer.testing import CliRunner

from phil.cli import main as cli
from phil.cli import memory as memory_cli
from phil.memory.agents_md import add_suggestions, read_suggestions
from phil.memory.doc import Bullet
from phil.memory.listing import memory_lines
from phil.memory.store import read_memory, update_memory
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run, update_run
from phil.ui.theme import make_console

runner = CliRunner()


def wide(monkeypatch):
    monkeypatch.setattr(memory_cli, "console", make_console(width=300))


def phil(repo, *args, **kwargs):
    return runner.invoke(cli.app, ["--repo", str(repo), "memory", *args], **kwargs)


def paths_of(repo):
    return ProjectPaths(resolve_repo(repo).slug)


def remember(repo, section, text, by_phil=False):
    update_memory(paths_of(repo), lambda doc: doc.add(Bullet(section, text, by_phil)))


def test_show_with_an_empty_memory(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo)
    assert result.exit_code == 0, result.output
    assert result.output == f"No memory yet for this repo.\nMemory file: {paths_of(calc_repo).memory_path}\n"


def test_add_then_show(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "add", "--section", "Commands", "uv run pytest -q")
    assert result.exit_code == 0, result.output
    assert result.output == "Added to Commands.\n"
    assert phil(calc_repo, "add", "Tests live in tests/").output == "Added to Conventions.\n"
    shown = phil(calc_repo).output.splitlines()
    assert shown[:4] == ["Commands", "  1. uv run pytest -q  (you)", "Conventions", "  2. Tests live in tests/  (you)"]
    assert shown[4] == f"Memory file: {paths_of(calc_repo).memory_path}"


def test_show_marks_phils_bullets_and_prints_markup_as_text(calc_repo, monkeypatch):
    wide(monkeypatch)
    remember(calc_repo, "Gotchas", "[bold]careful[/bold] with [red]x", by_phil=True)
    assert "  1. [bold]careful[/bold] with [red]x  (phil)" in phil(calc_repo).output.splitlines()


def test_add_to_an_unknown_section_lists_the_sections(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "add", "--section", "Nope", "something")
    assert result.exit_code == 1
    assert "Nope" in result.output
    for name in ("Commands", "Layout", "Conventions", "Gotchas"):
        assert name in result.output
    assert read_memory(paths_of(calc_repo)).bullets() == []


def test_add_refuses_a_secret(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "add", "token is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")
    assert result.exit_code == 1
    assert result.output == "Not added: looks like it contains a secret.\n"
    assert read_memory(paths_of(calc_repo)).bullets() == []


def test_add_to_an_unrecognised_file_still_adds(calc_repo, monkeypatch):
    wide(monkeypatch)
    path = paths_of(calc_repo).memory_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("just some notes\n", encoding="utf-8", newline="\n")
    assert phil(calc_repo, "add", "a fact").exit_code == 0
    assert [b.text for b in read_memory(paths_of(calc_repo)).bullets()] == ["a fact"]


def test_remove_a_bullet(calc_repo, monkeypatch):
    wide(monkeypatch)
    remember(calc_repo, "Commands", "one")
    remember(calc_repo, "Gotchas", "two")
    result = phil(calc_repo, "remove", "2")
    assert result.exit_code == 0, result.output
    assert result.output == "Removed: two\n"
    assert [b.text for b in read_memory(paths_of(calc_repo)).bullets()] == ["one"]


def test_remove_a_missing_bullet(calc_repo, monkeypatch):
    wide(monkeypatch)
    for n in ("1", "0"):
        result = phil(calc_repo, "remove", n)
        assert result.exit_code == 1
        assert result.output == f"No bullet {n}. phil memory lists them.\n"


def test_clear_with_yes_keeps_the_other_lines(calc_repo, monkeypatch):
    wide(monkeypatch)
    remember(calc_repo, "Commands", "one")
    remember(calc_repo, "Gotchas", "two", by_phil=True)
    result = phil(calc_repo, "clear", "--yes")
    assert result.exit_code == 0, result.output
    assert result.output == "Cleared 2 bullet(s).\n"
    text = paths_of(calc_repo).memory_path.read_text(encoding="utf-8")
    assert "## Commands" in text and "## Gotchas" in text and "one" not in text and "two" not in text


def test_clear_asks_first(calc_repo, monkeypatch):
    wide(monkeypatch)
    remember(calc_repo, "Commands", "one")
    result = phil(calc_repo, "clear", input="n\n")
    assert result.output.endswith("Nothing changed.\n")
    assert [b.text for b in read_memory(paths_of(calc_repo)).bullets()] == ["one"]
    assert phil(calc_repo, "clear", input="y\n").exit_code == 0
    assert read_memory(paths_of(calc_repo)).bullets() == []


def test_suggestions_lists_them_or_says_there_are_none(calc_repo, monkeypatch):
    wide(monkeypatch)
    assert phil(calc_repo, "suggestions").output == "No suggestions for AGENTS.md.\n"
    add_suggestions(paths_of(calc_repo), ["Use uv", "Run ruff"])
    assert phil(calc_repo, "suggestions").output == "1. Use uv\n2. Run ruff\n"
    assert "2 suggestion(s) for AGENTS.md: phil memory suggestions" in phil(calc_repo).output


def test_apply_with_yes_creates_agents_md(calc_repo, monkeypatch):
    wide(monkeypatch)
    remember(calc_repo, "Conventions", "Use uv", by_phil=True)
    add_suggestions(paths_of(calc_repo), ["Use uv", "Run ruff"])
    result = phil(calc_repo, "apply", "--yes")
    assert result.exit_code == 0, result.output
    target = calc_repo / "AGENTS.md"
    assert f"Added 2 line(s) to {target}. Not committed: review and commit it yourself.\n" in result.output
    assert "- Run ruff\n" in target.read_text(encoding="utf-8")
    assert read_suggestions(paths_of(calc_repo)) == []
    assert read_memory(paths_of(calc_repo)).bullets() == []


def test_apply_confirms_twice_without_an_agents_md(calc_repo, monkeypatch):
    wide(monkeypatch)
    add_suggestions(paths_of(calc_repo), ["Use uv"])
    result = phil(calc_repo, "apply", input="y\nn\n")
    assert "Append these to AGENTS.md?" in result.output
    assert "There's no AGENTS.md. Create it?" in result.output
    assert not (calc_repo / "AGENTS.md").exists()
    assert read_suggestions(paths_of(calc_repo)) == ["Use uv"]
    assert phil(calc_repo, "apply", input="y\ny\n").exit_code == 0
    assert (calc_repo / "AGENTS.md").exists()


def test_apply_to_an_existing_file_asks_once(calc_repo, monkeypatch):
    wide(monkeypatch)
    (calc_repo / "AGENTS.md").write_text("# Notes\n", encoding="utf-8", newline="\n")
    add_suggestions(paths_of(calc_repo), ["Use uv"])
    result = phil(calc_repo, "apply", input="y\n")
    assert result.exit_code == 0, result.output
    assert "Create it?" not in result.output


def test_apply_with_nothing_pending_exits_1(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "apply", "--yes")
    assert result.exit_code == 1
    assert result.output == "no suggestions\n"


def test_apply_is_refused_while_a_run_is_in_progress(calc_repo, monkeypatch, tmp_path):
    wide(monkeypatch)
    add_suggestions(paths_of(calc_repo), ["Use uv"])
    conn = connect(paths_of(calc_repo).db_path)
    try:
        create_run(conn, run_id="r-0001", keyword="a", base_sha="x", worktree=tmp_path / "w", tasks_total=1)
        update_run(conn, "r-0001", state="running")
    finally:
        conn.close()
    result = phil(calc_repo, "apply", "--yes")
    assert result.exit_code == 1
    assert result.output == "Run r-0001 is in progress; apply suggestions when it finishes.\n"
    assert not (calc_repo / "AGENTS.md").exists()
    assert read_suggestions(paths_of(calc_repo)) == ["Use uv"]


def test_dismiss(calc_repo, monkeypatch):
    wide(monkeypatch)
    add_suggestions(paths_of(calc_repo), ["one", "two"])
    result = phil(calc_repo, "dismiss")
    assert result.exit_code == 0, result.output
    assert result.output == "Dismissed 2 suggestion(s).\n"
    assert read_suggestions(paths_of(calc_repo)) == []


def test_edit_opens_the_file_and_shows_the_result(calc_repo, monkeypatch):
    wide(monkeypatch)
    opened = []

    def fake_edit(filename):
        opened.append(filename)
        with open(filename, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("- added by hand\n")

    monkeypatch.setattr(memory_cli, "edit_file", fake_edit)
    result = phil(calc_repo, "edit")
    assert result.exit_code == 0, result.output
    assert opened == [str(paths_of(calc_repo).memory_path)]
    assert memory_lines(paths_of(calc_repo)) == result.output.splitlines()
    assert "  1. added by hand  (you)" in result.output.splitlines()  # the template ends under Gotchas


def test_edit_adds_bullets_under_a_section(calc_repo, monkeypatch):
    wide(monkeypatch)

    def fake_edit(filename):
        with open(filename, encoding="utf-8") as handle:
            text = handle.read().replace("## Gotchas\n", "## Gotchas\n- watch out\n")
        with open(filename, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    monkeypatch.setattr(memory_cli, "edit_file", fake_edit)
    result = phil(calc_repo, "edit")
    assert result.exit_code == 0, result.output
    assert "  1. watch out  (you)" in result.output.splitlines()


def test_edit_reports_a_missing_editor(calc_repo, monkeypatch):
    wide(monkeypatch)

    def no_editor(filename):
        raise memory_cli.EditorError("no editor found; set $EDITOR")

    monkeypatch.setattr(memory_cli, "edit_file", no_editor)
    result = phil(calc_repo, "edit")
    assert result.exit_code == 1
    assert result.output == "Couldn't open the editor: no editor found; set $EDITOR\n"
