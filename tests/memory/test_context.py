from dataclasses import replace

from phil.agents.registry import SPECS
from phil.agents.spec import load_prompt
from phil.memory.context import AGENT_FILE_MAX_BYTES, TRUNCATED_NOTE, build_repo_context, find_agent_file
from phil.memory.doc import MAX_FILE_BYTES, Bullet
from phil.memory.store import update_memory
from phil.store.paths import ProjectPaths


def test_agents_md_is_preferred_over_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("claude\n", encoding="utf-8")
    assert find_agent_file(tmp_path).name == "CLAUDE.md"
    (tmp_path / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    assert find_agent_file(tmp_path).name == "AGENTS.md"


def test_a_directory_named_agents_md_is_not_an_agent_file(tmp_path):
    (tmp_path / "AGENTS.md").mkdir()
    assert find_agent_file(tmp_path) is None


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
    real = type(tmp_path / "AGENTS.md").read_bytes

    def boom(self):
        if self.name == "AGENTS.md":
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(type(tmp_path), "read_bytes", boom)
    built = build_repo_context(tmp_path, ProjectPaths("x", home=tmp_path / "home"))
    assert built.problems == ["couldn't read AGENTS.md: PermissionError: denied"]
    assert built.text == ""


def test_load_prompt_is_unchanged_without_context_and_appends_it_with():
    spec = SPECS["implementer"]
    plain = load_prompt(spec)
    assert load_prompt(replace(spec, context="")) == plain
    assert load_prompt(replace(spec, context="## Repo context\nx")) == plain.rstrip("\n") + "\n\n## Repo context\nx\n"


def test_which_roles_get_the_block():
    with_block = {name for name, spec in SPECS.items() if spec.repo_context}
    assert with_block == {"intake", "architect", "critic", "implementer", "tester", "reviewer", "btw",
                          "answer", "quick_implementer", "design"}
