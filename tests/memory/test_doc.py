from phil.memory.doc import MAX_FILE_BYTES, PHIL_MARK, SECTIONS, Bullet, MemoryDoc

SAMPLE = """# Phil memory — calc-1
<!-- Phil reads this before every agent call. Edit freely; keep it short. -->

## Commands
- Tests: `uv run pytest -q` <!-- phil -->
- Lint: `ruff check`

## My notes
- keep this as it is
Free paragraph text.

## gotchas
- PHIL_HOME in tests <!-- phil -->
"""


def test_round_trip_is_byte_identical():
    assert MemoryDoc.parse(SAMPLE).render() == SAMPLE


def test_bullets_know_their_section_and_owner():
    assert MemoryDoc.parse(SAMPLE).bullets() == [
        Bullet("Commands", "Tests: `uv run pytest -q`", True),
        Bullet("Commands", "Lint: `ruff check`", False),
        Bullet("Gotchas", "PHIL_HOME in tests", True),
    ]


def test_new_doc_has_the_template_and_no_bullets():
    doc = MemoryDoc.new("calc-1")
    assert doc.render().startswith("# Phil memory — calc-1\n")
    assert [line for line in doc.render().splitlines() if line.startswith("## ")] == [f"## {s}" for s in SECTIONS]
    assert doc.bullets() == [] and doc.prompt_text() == ""


def test_add_goes_after_the_sections_last_bullet_and_adds_a_missing_heading():
    doc = MemoryDoc.parse(SAMPLE)
    doc.add(Bullet("Commands", "Build: `make`", True))
    doc.add(Bullet("Layout", "src/ holds the package", True))
    text = doc.render()
    assert "- Lint: `ruff check`\n- Build: `make` <!-- phil -->\n" in text
    assert text.endswith("## Layout\n- src/ holds the package <!-- phil -->\n")


def test_remove_phil_only_never_removes_a_user_bullet():
    doc = MemoryDoc.parse(SAMPLE)
    assert doc.remove("Lint: `ruff check`", phil_only=True) is False
    assert doc.remove("Tests: `uv run pytest -q`", phil_only=True) is True
    assert [b.text for b in doc.bullets()] == ["Lint: `ruff check`", "PHIL_HOME in tests"]


def test_remove_index_is_one_based_in_bullet_order():
    doc = MemoryDoc.parse(SAMPLE)
    assert doc.remove_index(2).text == "Lint: `ruff check`"
    assert len(doc.bullets()) == 2


def test_fit_drops_only_phil_bullets_in_section_order():
    doc = MemoryDoc.new("x")
    for i in range(60):
        doc.add(Bullet("Commands", f"phil command {i:02d} " + "x" * 150, True))
    doc.add(Bullet("Gotchas", "a user gotcha", False))
    dropped = doc.fit()
    assert len(doc.render().encode("utf-8")) <= MAX_FILE_BYTES
    assert dropped and dropped[0].text.startswith("phil command 00")
    assert "a user gotcha" in [b.text for b in doc.bullets()]


def test_prompt_text_strips_title_comments_marks_and_empty_sections():
    text = MemoryDoc.parse(SAMPLE).prompt_text()
    assert PHIL_MARK not in text and "# Phil memory" not in text and "<!--" not in text
    assert "## Commands\n- Tests: `uv run pytest -q`\n- Lint: `ruff check`" in text
    assert "## My notes\n- keep this as it is\nFree paragraph text." in text
    assert "## Layout" not in MemoryDoc.new("x").prompt_text()


def test_annotated_marks_owners_for_the_memory_agent():
    assert "[phil] Tests: `uv run pytest -q`" in MemoryDoc.parse(SAMPLE).annotated()
    assert "[you] Lint: `ruff check`" in MemoryDoc.parse(SAMPLE).annotated()


def test_free_prose_is_not_recognised_but_still_reaches_prompts():
    doc = MemoryDoc.parse("Always run make first.\n")
    assert doc.recognised is False
    assert doc.prompt_text() == "Always run make first."
    assert MemoryDoc.parse("").recognised is True
