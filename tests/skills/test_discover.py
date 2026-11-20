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


def test_same_name_first_source_wins(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    write_skill(first, "x", "x", description="From agents")
    write_skill(second, "x", "x", description="From cursor")

    catalog = discover([source("agents", first), source("cursor", second)])

    assert [(skill.name, skill.source) for skill in catalog.skills] == [("x", "agents")]
    assert [(skill.name, skill.source) for skill in catalog.hidden] == [("x", "cursor")]
