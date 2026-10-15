import pytest

from phil.contracts import MemoryAdd, MemoryUpdate, SelfCheck
from phil.memory.apply import apply_update, check_line, looks_secret, memory_line
from phil.memory.doc import Bullet, MemoryDoc


@pytest.mark.parametrize("text", [
    "token sk-ant-abcdef1234567890", "use ghp_abcdefghijklmnop", "OPENAI_API_KEY=abc123",
    "password: hunter2", "-----BEGIN OPENSSH PRIVATE KEY-----", "id 3f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e",
])
def test_secrets_are_spotted(text):
    assert looks_secret(text)


@pytest.mark.parametrize("text", [
    "Tests: `uv run pytest -q`", "Set PHIL_HOME to a temp dir in tests", "src/phil/memory/doc.py holds the parser",
    "TOKEN_LIMIT is set in config.py",
])
def test_ordinary_lines_are_not_secrets(text):
    assert not looks_secret(text)


def test_check_line_reasons():
    assert check_line(" ") == "empty"
    assert check_line("a\nb") == "more than one line"
    assert check_line("x" * 201) == "longer than 200 characters"
    assert check_line("OPENAI_API_KEY=abc") == "looks like it contains a secret"
    assert check_line("Tests: `pytest`") is None


def doc() -> MemoryDoc:
    d = MemoryDoc.new("x")
    d.add(Bullet("Commands", "Tests: `pytest -q`", True))
    d.add(Bullet("Gotchas", "Mine, keep it", False))
    return d


def test_duplicates_of_memory_or_agent_file_are_ignored():
    update = MemoryUpdate(add=[
        MemoryAdd(section="Commands", text="tests:  `PYTEST -q`"),
        MemoryAdd(section="Layout", text="Source lives in src/"),
        MemoryAdd(section="Layout", text="Docs in docs/"),
    ])
    applied = apply_update(doc(), update, agent_file="# Agents\n- source lives in   SRC/\n", pending=[])
    assert [b.text for b in applied.added] == ["Docs in docs/"]


def test_user_bullets_are_never_removed():
    d = doc()
    applied = apply_update(d, MemoryUpdate(remove=["Mine, keep it", "Tests: `pytest -q`"]), agent_file="", pending=[])
    assert applied.removed == ["Tests: `pytest -q`"]
    assert [b.text for b in d.bullets()] == ["Mine, keep it"]


def test_at_most_eight_additions_and_five_suggestions():
    update = MemoryUpdate(
        add=[MemoryAdd(section="Gotchas", text=f"gotcha {i}") for i in range(12)],
        agents_md=[f"Run step {i}" for i in range(9)] + ["Run step 0"],
    )
    applied = apply_update(doc(), update, agent_file="", pending=["Run step 1"])
    assert len(applied.added) == 8
    assert applied.suggestions == ["Run step 0", "Run step 2", "Run step 3", "Run step 4", "Run step 5"]


def test_memory_line():
    applied = apply_update(doc(), MemoryUpdate(
        add=[MemoryAdd(section="Commands", text="Build: `node build.mjs` writes dist/index.html for the site")],
        remove=["Tests: `pytest -q`"], agents_md=["Build with `node build.mjs`"],
    ), agent_file="", pending=[])
    assert memory_line(applied) == (
        "Memory: +1 (Commands: Build: `node build.mjs` writes dist/inde…) · −1 · "
        "1 suggestion for AGENTS.md · /memory to review"
    )
    assert memory_line(apply_update(doc(), MemoryUpdate(), agent_file="", pending=[])) is None


def test_self_check_memory_notes_defaults_empty():
    assert SelfCheck(assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[]).memory_notes == []
