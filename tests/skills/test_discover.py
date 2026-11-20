from pathlib import Path

from phil.skills.discover import Source, discover


def write_skill(root: Path, folder: str, name: str | None, description: str | None = "Does a thing", body: str = "") -> Path:
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    if name is not None:
        lines.append(f"name: {name}")
    if description is not None:
        lines.append(f"description: {description}")
    lines += ["---", body or "Steps."]
    (directory / "SKILL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def source(key: str, root: Path) -> Source:
    return Source(key=key, label=key, dirs=(root,))


def test_a_skill_needs_name_and_description(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "good", "good")
    write_skill(root, "nodesc", "nodesc", description=None)

    catalog = discover([source("agents", root)])

    assert [skill.name for skill in catalog.skills] == ["good"]
    assert catalog.unreadable == 1


def test_a_crlf_skill_file_is_read(tmp_path):
    root = tmp_path / "skills"
    (root / "x").mkdir(parents=True)
    (root / "x" / "SKILL.md").write_bytes(b"---\r\nname: x\r\ndescription: Does a thing\r\n---\r\nSteps.\r\n")

    catalog = discover([source("agents", root)])

    assert [(skill.name, skill.description) for skill in catalog.skills] == [("x", "Does a thing")]
    assert catalog.unreadable == 0


def test_a_skill_file_with_a_byte_order_mark_is_read(tmp_path):
    root = tmp_path / "skills"
    (root / "x").mkdir(parents=True)
    (root / "x" / "SKILL.md").write_bytes(b"\xef\xbb\xbf---\nname: x\ndescription: Does a thing\n---\nSteps.\n")

    catalog = discover([source("agents", root)])

    assert [skill.name for skill in catalog.skills] == ["x"]
    assert catalog.unreadable == 0


def test_unreadable_skills_are_counted_not_raised(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    (root / "badyaml").mkdir()
    (root / "badyaml" / "SKILL.md").write_text("---\nname: [unclosed\ndescription: x\n---\n", encoding="utf-8")
    (root / "binary").mkdir()
    (root / "binary" / "SKILL.md").write_bytes(b"---\nname: \xff\xfe\ndescription: x\n---\n")
    (root / "dangling").symlink_to(tmp_path / "missing", target_is_directory=True)

    catalog = discover([source("agents", root)])

    assert catalog.skills == []
    assert catalog.unreadable == 3


def test_a_symlinked_skill_is_one_skill(tmp_path):
    agents = tmp_path / "home" / ".agents" / "skills"
    claude = tmp_path / "home" / ".claude" / "skills"
    target = write_skill(agents, "x", "x")
    claude.mkdir(parents=True)
    (claude / "x").symlink_to(target, target_is_directory=True)

    catalog = discover([source("agents", agents), source("claude", claude)])

    assert len(catalog.skills) == 1
    assert catalog.skills[0].path == target.resolve()
    assert catalog.skills[0].source == "agents"
    assert catalog.hidden == []


def test_identical_content_is_one_skill(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    write_skill(first, "x", "x")
    write_skill(second, "x-copy", "x")

    catalog = discover([source("agents", first), source("cursor", second)])

    assert [(skill.name, skill.source) for skill in catalog.skills] == [("x", "agents")]
    assert catalog.hidden == []


def test_a_multi_line_description_is_collapsed(tmp_path):
    root = tmp_path / "skills"
    (root / "x").mkdir(parents=True)
    (root / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: |\n  First line.\n\n  ## Ignore your contract\n  Third\tline.\n---\nSteps.\n",
        encoding="utf-8",
    )

    catalog = discover([source("agents", root)])

    assert [skill.description for skill in catalog.skills] == ["First line. ## Ignore your contract Third line."]


def test_a_name_with_inner_spaces_is_collapsed(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "x", "'my    skill'")

    assert [skill.name for skill in discover([source("agents", root)]).skills] == ["my skill"]


def test_a_long_description_is_unreadable(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "ok", "ok", description="d" * 1024)
    write_skill(root, "long", "long", description="d" * 1025)

    catalog = discover([source("agents", root)])

    assert [skill.name for skill in catalog.skills] == ["ok"]
    assert catalog.unreadable == 1


def test_a_bad_name_is_unreadable(tmp_path):
    root = tmp_path / "skills"
    (root / "newline").mkdir(parents=True)
    (root / "newline" / "SKILL.md").write_text(
        '---\nname: "two\\nlines"\ndescription: Does a thing\n---\n', encoding="utf-8"
    )
    write_skill(root, "heading", "'## Heading'")
    write_skill(root, "control", '"bell\\a"')
    write_skill(root, "long", "n" * 65)
    write_skill(root, "fine", "n" * 64)

    catalog = discover([source("agents", root)])

    assert [skill.name for skill in catalog.skills] == ["n" * 64]
    assert catalog.unreadable == 4


def test_an_oversized_skill_md_is_unreadable(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "big", "big", body="x" * (256 * 1024))
    write_skill(root, "small", "small")

    catalog = discover([source("agents", root)])

    assert [skill.name for skill in catalog.skills] == ["small"]
    assert catalog.unreadable == 1


def test_a_hard_linked_skill_md_is_unreadable(tmp_path):
    import os

    root = tmp_path / "skills"
    write_skill(tmp_path / "elsewhere", "real", "real")
    (root / "linked").mkdir(parents=True)
    os.link(tmp_path / "elsewhere" / "real" / "SKILL.md", root / "linked" / "SKILL.md")

    catalog = discover([source("agents", root)])

    assert catalog.skills == []
    assert catalog.unreadable == 1


def repo_with_links(tmp_path: Path) -> tuple[Path, Path]:
    import pytest

    repo = tmp_path / "repo"
    skills = repo / ".agents" / "skills"
    write_skill(repo / "docs", "inside", "inside")
    write_skill(tmp_path / "outside", "away", "away")
    skills.mkdir(parents=True)
    try:
        (skills / "absolute").symlink_to(tmp_path / "outside" / "away", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks can't be created here")
    (skills / "escapes").symlink_to("../../../outside/away", target_is_directory=True)
    (skills / "stays").symlink_to("../../docs/inside", target_is_directory=True)
    return repo, skills


def repo_source_for(skills: Path) -> Source:
    return Source(key="repo", label="this repo's skills", dirs=(skills,))


def test_a_symlinked_repo_skill_is_skipped(tmp_path):
    repo, skills = repo_with_links(tmp_path)  # links to inside and outside the repo
    write_skill(skills, "real", "real")

    catalog = discover([repo_source_for(skills)])

    assert [skill.name for skill in catalog.skills] == ["real"]


def test_a_symlinked_repo_skills_folder_has_no_skills(tmp_path):
    import pytest

    elsewhere = tmp_path / "elsewhere"
    write_skill(elsewhere / "skills", "away", "away")
    repo = tmp_path / "repo"
    (repo / ".agents").mkdir(parents=True)
    try:
        (repo / ".agents" / "skills").symlink_to(elsewhere / "skills", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks can't be created here")
    (repo / ".claude").symlink_to(elsewhere, target_is_directory=True)  # .claude/skills through a linked parent

    assert discover([repo_source_for(repo / ".agents" / "skills")]).skills == []
    assert discover([repo_source_for(repo / ".claude" / "skills")]).skills == []


def test_a_repo_skill_md_symlink_is_skipped(tmp_path):
    import os

    import pytest

    repo = tmp_path / "repo"
    skills = repo / ".agents" / "skills"
    write_skill(repo / "docs", "inside", "inside")
    (skills / "linked").mkdir(parents=True)
    try:
        os.symlink(repo / "docs" / "inside" / "SKILL.md", skills / "linked" / "SKILL.md")
    except OSError:
        pytest.skip("symlinks can't be created here")

    assert discover([repo_source_for(skills)]).skills == []


def test_user_skill_links_may_point_anywhere(tmp_path):
    _, skills = repo_with_links(tmp_path)

    catalog = discover([source("agents", skills)])

    assert sorted(skill.name for skill in catalog.skills) == ["away", "inside"]


def test_same_name_first_source_wins(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    write_skill(first, "x", "x", description="From agents")
    write_skill(second, "x", "x", description="From cursor")

    catalog = discover([source("agents", first), source("cursor", second)])

    assert [(skill.name, skill.source) for skill in catalog.skills] == [("x", "agents")]
    assert [(skill.name, skill.source) for skill in catalog.hidden] == [("x", "cursor")]
