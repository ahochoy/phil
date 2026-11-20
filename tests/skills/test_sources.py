import json
from pathlib import Path

import pytest

from phil.config import PhilConfig, SkillsConfig
from phil.skills.discover import discover
from phil.skills.sources import (
    TOO_LARGE,
    approved_sources,
    repo_approved,
    repo_digest,
    repo_source,
    set_repo_approved,
    user_sources,
)
from phil.store.paths import ProjectPaths


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def config(*folders: str) -> PhilConfig:
    return PhilConfig(skills=SkillsConfig(folders=list(folders)))


def test_plugin_skills_come_from_installed_paths_only(home):
    plugins = home / ".claude" / "plugins"
    installed = plugins / "cache" / "p" / "1.0" / "skills"
    stale = plugins / "cache" / "p" / "0.9" / "skills"
    installed.mkdir(parents=True)
    stale.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"p@m": [{"scope": "user", "installPath": str(installed.parent)}]}}),
        encoding="utf-8",
    )

    by_key = {source.key: source for source in user_sources()}

    assert by_key["claude-plugins"].dirs == (installed,)


def test_malformed_plugin_list_means_no_plugin_source(home):
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text("{not json", encoding="utf-8")

    assert "claude-plugins" not in {source.key for source in user_sources()}


def test_user_sources_are_only_those_that_exist_in_order(home):
    (home / ".cursor" / "skills").mkdir(parents=True)
    (home / ".agents" / "skills").mkdir(parents=True)

    assert [source.key for source in user_sources()] == ["agents", "cursor"]


def write_skill(root: Path, folder: str, name: str, description: str = "Does a thing") -> None:
    (root / folder).mkdir(parents=True)
    (root / folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\nSteps.\n", encoding="utf-8")


def test_repo_skills_need_repo_approval(home, tmp_path):
    repo = tmp_path / "repo"
    write_skill(repo / ".agents" / "skills", "r", "r")
    (repo / ".claude" / "skills").mkdir(parents=True)
    (home / ".agents" / "skills").mkdir(parents=True)
    paths = ProjectPaths(slug="p", home=tmp_path / "phil_home")
    cfg = config("agents")

    assert not repo_approved(paths, repo)
    assert [s.key for s in approved_sources(cfg, repo, paths)] == ["agents"]

    set_repo_approved(paths, True, repo_digest(repo))

    assert repo_approved(paths, repo)
    sources = approved_sources(cfg, repo, paths)
    assert [s.key for s in sources] == ["agents", "repo"]
    assert sources[1].dirs == (repo / ".agents" / "skills", repo / ".claude" / "skills")

    set_repo_approved(paths, False)
    assert not repo_approved(paths, repo)


def test_approval_without_a_digest_does_not_hold(tmp_path):
    repo = tmp_path / "repo"
    write_skill(repo / ".agents" / "skills", "r", "r")
    paths = ProjectPaths(slug="p", home=tmp_path / "phil_home")

    set_repo_approved(paths, True)

    assert not repo_approved(paths, repo)


def test_repo_approval_holds_when_nothing_changed(tmp_path):
    repo = tmp_path / "repo"
    write_skill(repo / ".agents" / "skills", "r", "r")
    paths = ProjectPaths(slug="p", home=tmp_path / "phil_home")

    set_repo_approved(paths, True, repo_digest(repo))

    assert repo_approved(paths, repo)
    assert repo_digest(repo) == repo_digest(repo)


def test_repo_approval_is_void_after_a_skill_changes(tmp_path):
    repo = tmp_path / "repo"
    write_skill(repo / ".agents" / "skills", "r", "r")
    paths = ProjectPaths(slug="p", home=tmp_path / "phil_home")
    set_repo_approved(paths, True, repo_digest(repo))

    (repo / ".agents" / "skills" / "r" / "SKILL.md").write_text(
        "---\nname: r\ndescription: Now different\n---\nSteps.\n", encoding="utf-8"
    )
    assert not repo_approved(paths, repo)

    set_repo_approved(paths, True, repo_digest(repo))
    write_skill(repo / ".agents" / "skills", "new", "new")
    assert not repo_approved(paths, repo)


def approved_repo_with_reference(tmp_path):
    repo = tmp_path / "repo"
    skills = repo / ".agents" / "skills"
    write_skill(skills, "r", "r")
    (skills / "r" / "references").mkdir()
    (skills / "r" / "references" / "guide.md").write_text("original", encoding="utf-8")
    paths = ProjectPaths(slug="p", home=tmp_path / "phil_home")
    set_repo_approved(paths, True, repo_digest(repo))
    assert repo_approved(paths, repo)
    return repo, skills / "r", paths


def test_repo_approval_is_void_after_a_reference_file_changes(tmp_path):
    repo, skill, paths = approved_repo_with_reference(tmp_path)

    (skill / "references" / "guide.md").write_text("changed", encoding="utf-8")

    assert not repo_approved(paths, repo)


def test_repo_approval_is_void_after_a_file_is_added_to_a_skill(tmp_path):
    repo, skill, paths = approved_repo_with_reference(tmp_path)

    (skill / "references" / "extra.md").write_text("new", encoding="utf-8")

    assert not repo_approved(paths, repo)


def test_repo_approval_is_void_after_a_symlink_target_changes(tmp_path):
    repo, skill, paths = approved_repo_with_reference(tmp_path)
    link = skill / "references" / "link.md"
    try:
        link.symlink_to("guide.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks cannot be created here")
    set_repo_approved(paths, True, repo_digest(repo))
    assert repo_approved(paths, repo)

    link.unlink()
    link.symlink_to("elsewhere.md")

    assert not repo_approved(paths, repo)


def test_an_oversized_skill_folder_never_approves(tmp_path, monkeypatch):
    repo, skill, paths = approved_repo_with_reference(tmp_path)
    monkeypatch.setattr("phil.skills.sources.MAX_DIGEST_ENTRIES", 2)

    set_repo_approved(paths, True, repo_digest(repo))

    assert not repo_approved(paths, repo)


def test_a_skill_folder_over_the_size_limit_never_approves(tmp_path, monkeypatch):
    repo, skill, paths = approved_repo_with_reference(tmp_path)
    monkeypatch.setattr("phil.skills.sources.MAX_DIGEST_BYTES", 5)

    set_repo_approved(paths, True, repo_digest(repo))

    assert not repo_approved(paths, repo)


def test_a_repo_skill_cannot_replace_a_user_skill(home, tmp_path):
    repo = tmp_path / "repo"
    write_skill(repo / ".agents" / "skills", "x", "x", description="From the repo")
    write_skill(home / ".agents" / "skills", "x", "x", description="From the user")
    paths = ProjectPaths(slug="p", home=tmp_path / "phil_home")
    set_repo_approved(paths, True, repo_digest(repo))

    catalog = discover(approved_sources(config("agents"), repo, paths))

    assert [(s.name, s.source, s.description) for s in catalog.skills] == [("x", "agents", "From the user")]
    assert [(s.name, s.source) for s in catalog.hidden] == [("x", "repo")]


def test_a_symlinked_repo_skills_folder_never_approves(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    write_skill(elsewhere / "skills", "x", "x")
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        (repo / ".agents").symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks cannot be created here")
    paths = ProjectPaths(slug="p", home=tmp_path / "phil_home")

    assert repo_digest(repo) == TOO_LARGE
    assert repo_source(repo) is None
    set_repo_approved(paths, True, repo_digest(repo))
    assert not repo_approved(paths, repo)


def test_user_sources_have_friendly_labels(home):
    for folder in (".agents", ".claude", ".codex", ".cursor", ".gemini"):
        (home / folder / "skills").mkdir(parents=True)

    assert [source.label for source in user_sources()] == [
        "Shared skills (npx skills)", "Claude Code", "Codex", "Cursor", "Gemini",
    ]


def test_repo_source_is_none_without_skill_folders(tmp_path):
    assert repo_source(tmp_path) is None


def test_set_repo_approved_keeps_other_keys(tmp_path):
    paths = ProjectPaths(slug="p", home=tmp_path / "phil_home")
    paths.project_dir.mkdir(parents=True)
    paths.skills_approval_path.write_text("mentioned = true\n", encoding="utf-8")

    set_repo_approved(paths, True)

    text = paths.skills_approval_path.read_text(encoding="utf-8")
    assert "mentioned = true" in text
    assert "approved = true" in text


def test_only_approved_user_folders_count(home):
    for folder in (".agents", ".claude", ".cursor"):
        (home / folder / "skills").mkdir(parents=True)

    assert [s.key for s in approved_sources(config("claude"), None, None)] == ["claude"]


def test_folders_match_by_tilde_path_too(home):
    for folder in (".agents", ".claude"):
        (home / folder / "skills").mkdir(parents=True)

    sources = approved_sources(config("~/.agents/skills"), None, None)

    assert [s.key for s in sources] == ["agents"]


def test_the_repo_mention_is_remembered_by_digest_and_keeps_approval(tmp_path):
    from phil.skills.sources import repo_mentioned, set_repo_mentioned

    paths = ProjectPaths(slug="p", home=tmp_path / "phil_home")
    assert repo_mentioned(paths) is None
    set_repo_approved(paths, False)

    set_repo_mentioned(paths, "abc")

    assert repo_mentioned(paths) == "abc"
    assert "approved = false" in paths.skills_approval_path.read_text(encoding="utf-8")
    set_repo_approved(paths, True, "abc")
    assert repo_mentioned(paths) == "abc"


def test_an_old_boolean_mention_counts_as_not_mentioned(tmp_path):
    from phil.skills.sources import repo_mentioned

    paths = ProjectPaths(slug="p", home=tmp_path / "phil_home")
    paths.project_dir.mkdir(parents=True)
    paths.skills_approval_path.write_text("mentioned = true\n", encoding="utf-8")

    assert repo_mentioned(paths) is None
