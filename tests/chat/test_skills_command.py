"""/skills, the skill folders in /permissions, and the once-only mention of a repo's own skills."""

import tomllib
from pathlib import Path

import pytest

from phil.chat.controller import HELP_COMMANDS
from phil.config import PhilConfig, SkillsConfig, global_config_path
from phil.repo import resolve_repo
from phil.skills.discover import Catalog, Skill
from phil.skills.labels import LabelCache
from phil.skills.runtime import SkillRuntime
from phil.skills.sources import repo_digest, repo_mentioned, set_repo_approved
from phil.store.paths import ProjectPaths
from tests.chat.test_controller import classic_config, run_chat

MENTION = "This repo ships its own skills; Phil won't use them until you run phil skills repo on."


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def flat(text: str) -> str:
    return " ".join(text.split())


def with_skills(**skills) -> PhilConfig:
    return classic_config().model_copy(update={"skills": SkillsConfig(**skills)})


def write_skill(folder: Path, name: str, body: str = "Body") -> None:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} helps\n---\n{body}\n", encoding="utf-8")


def paths_of(repo) -> ProjectPaths:
    return ProjectPaths(resolve_repo(repo).slug)


def test_skills_prints_the_listing_and_points_to_phil_skills(calc_repo, home, monkeypatch):
    (home / ".agents" / "skills").mkdir(parents=True)
    layout = Skill("layout", "layout helps", Path("/s/layout"), "agents", "hash-layout")
    cache = LabelCache()
    cache.put("hash-layout", ["design"])
    runtime = SkillRuntime(catalog=Catalog(skills=[layout]), cache=cache)
    monkeypatch.setattr("phil.chat.controller.load_runtime", lambda config, root, paths: runtime)
    monkeypatch.setattr("phil.chat.controller.label_skills", lambda *a, **k: 0)

    text, *_ = run_chat(calc_repo, ["/skills"], {}, config=with_skills(folders=["agents"]))

    assert "Skill folders: ~/.agents/skills" in text
    assert "layout  design (label)" in text
    assert text.index("layout  design (label)") < text.index("Change these with phil skills.")
    assert not global_config_path().exists()  # read-only


def test_skills_with_nothing_approved(calc_repo):
    text, *_ = run_chat(calc_repo, ["/skills"], {})
    assert "No skill folders are approved. Run phil setup to choose them." in text
    assert "Change these with phil skills." in text


def test_skills_points_to_the_repo_rule_when_the_repo_ships_skills(calc_repo):
    write_skill(calc_repo / ".agents" / "skills" / "mine", "mine")
    text, *_ = run_chat(calc_repo, ["/skills"], {})
    assert "turn them on with: phil skills repo on" in flat(text)


def test_help_lists_skills():
    assert dict(HELP_COMMANDS)["/skills"] == "the skills Phil can use"


def test_permissions_lists_the_skill_folders(calc_repo, home):
    (home / ".agents" / "skills").mkdir(parents=True)
    text, *_ = run_chat(calc_repo, ["/permissions"], {}, config=with_skills(folders=["agents"]))
    assert "Skill folders Phil's agents can read:" in text
    assert "  ~/.agents/skills" in text


def test_a_repo_with_its_own_skills_is_mentioned_once_and_remembered_by_digest(calc_repo):
    write_skill(calc_repo / ".agents" / "skills" / "mine", "mine")

    first, *_ = run_chat(calc_repo, [], {})
    second, *_ = run_chat(calc_repo, [], {})

    assert MENTION in flat(first)
    assert MENTION not in flat(second)
    assert repo_mentioned(paths_of(calc_repo)) == repo_digest(calc_repo)


def test_changed_repo_skills_are_mentioned_again(calc_repo):
    folder = calc_repo / ".agents" / "skills" / "mine"
    write_skill(folder, "mine")
    run_chat(calc_repo, [], {})

    (folder / "SKILL.md").write_text("---\nname: mine\ndescription: mine helps\n---\nChanged\n", encoding="utf-8")
    again, *_ = run_chat(calc_repo, [], {})

    assert MENTION in flat(again)
    assert repo_mentioned(paths_of(calc_repo)) == repo_digest(calc_repo)


def test_no_mention_without_repo_skills(calc_repo):
    text, *_ = run_chat(calc_repo, [], {})
    assert "ships its own skills" not in text
    assert repo_mentioned(paths_of(calc_repo)) is None


def test_no_mention_when_the_repo_skills_are_approved(calc_repo, monkeypatch):
    monkeypatch.setattr("phil.chat.controller.label_skills", lambda *a, **k: 0)  # the approved skills would be labelled
    write_skill(calc_repo / ".agents" / "skills" / "mine", "mine")
    set_repo_approved(paths_of(calc_repo), True, repo_digest(calc_repo))
    text, *_ = run_chat(calc_repo, [], {})
    assert "ships its own skills" not in text
    stored = tomllib.loads(paths_of(calc_repo).skills_approval_path.read_text(encoding="utf-8"))
    assert "mentioned" not in stored
