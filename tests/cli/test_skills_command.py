"""`phil skills`: list the skills, turn one on or off, set its stages, and turn the repo's own skills on or off."""

import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from phil.cli import main as cli
from phil.cli import skills as skills_cli
from phil.config import global_config_path
from phil.repo import resolve_repo
from phil.skills.labels import LabelCache
from phil.skills.sources import TOO_LARGE, repo_approved, repo_digest, set_repo_approved
from phil.store.paths import ProjectPaths
from phil.ui.theme import make_console

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(skills_cli, "console", make_console(width=300))
    global_config_path().parent.mkdir(parents=True, exist_ok=True)
    return home


def phil(repo, *args):
    return runner.invoke(cli.app, ["--repo", str(repo), "skills", *args])


def write_skill(folder: Path, name: str, description: str | None = None) -> None:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description or name + ' helps'}\n---\nBody\n", encoding="utf-8"
    )


def config_data() -> dict:
    return tomllib.loads(global_config_path().read_text(encoding="utf-8"))


def paths_of(repo) -> ProjectPaths:
    return ProjectPaths(resolve_repo(repo).slug)


def test_with_nothing_approved_it_says_how_to_choose(calc_repo):
    result = phil(calc_repo)
    assert result.exit_code == 0, result.output
    assert "No skill folders are approved. Run phil setup to choose them." in result.output


def test_lists_the_skills_of_an_approved_folder(calc_repo, home):
    write_skill(home / ".agents" / "skills" / "layout", "layout")
    global_config_path().write_text('[skills]\nfolders = ["agents"]\n', encoding="utf-8")
    result = phil(calc_repo)
    assert result.exit_code == 0, result.output
    assert "Skill folders: ~/.agents/skills" in result.output
    assert "Not labelled yet: 1" in result.output


def test_a_labelled_skill_shows_its_stages(calc_repo, home):
    write_skill(home / ".agents" / "skills" / "layout", "layout")
    global_config_path().write_text('[skills]\nfolders = ["agents"]\n', encoding="utf-8")
    from phil.skills.discover import discover
    from phil.skills.sources import user_sources

    [skill] = discover(user_sources()).skills
    cache = LabelCache()
    cache.put(skill.content_hash, ["design", "testing"])
    cache.save()
    result = phil(calc_repo)
    assert "layout  design, testing (label)" in result.output


def test_a_repo_with_its_own_skills_points_to_repo_on(calc_repo):
    write_skill(calc_repo / ".agents" / "skills" / "mine", "mine")
    result = phil(calc_repo)
    assert "This repo has its own skills; turn them on with: phil skills repo on" in result.output


def test_off_adds_the_skill_and_keeps_the_rest_of_the_file(calc_repo, home):
    write_skill(home / ".agents" / "skills" / "layout", "layout")
    global_config_path().write_text(
        '# my notes\n[models]\nhigh = "x/y"\n\n[skills]\nfolders = ["agents"]  # chosen in setup\n', encoding="utf-8"
    )
    result = phil(calc_repo, "off", "layout")
    assert result.exit_code == 0, result.output
    assert "Turned off layout." in result.output
    assert "Phil hasn't found" not in result.output
    text = global_config_path().read_text(encoding="utf-8")
    assert "# my notes" in text and "# chosen in setup" in text
    assert config_data()["skills"] == {"folders": ["agents"], "off": ["layout"]}
    assert config_data()["models"] == {"high": "x/y"}
    assert "Off: layout" in phil(calc_repo).output


def test_off_twice_lists_the_skill_once(calc_repo):
    phil(calc_repo, "off", "layout")
    phil(calc_repo, "off", "layout")
    assert config_data()["skills"]["off"] == ["layout"]


def test_on_removes_the_skill_from_off(calc_repo):
    global_config_path().write_text('[skills]\noff = ["layout", "tests"]\n', encoding="utf-8")
    result = phil(calc_repo, "on", "layout")
    assert result.exit_code == 0, result.output
    assert "Turned on layout." in result.output
    assert config_data()["skills"]["off"] == ["tests"]


def test_a_skill_phil_hasnt_found_is_saved_with_a_note(calc_repo):
    result = phil(calc_repo, "off", "later")
    assert result.exit_code == 0, result.output
    assert "Phil hasn't found a skill named later; the setting is saved anyway." in result.output
    assert config_data()["skills"]["off"] == ["later"]


def test_stages_sets_an_override(calc_repo):
    result = phil(calc_repo, "stages", "layout", "design, testing")
    assert result.exit_code == 0, result.output
    assert "layout now helps these stages: design, testing." in result.output
    assert config_data()["skills"]["stages"] == {"layout": ["design", "testing"]}


def test_stages_dash_clears_the_override(calc_repo):
    global_config_path().write_text(
        '[skills]\nfolders = ["agents"]\n\n[skills.stages]\nlayout = ["design"]\nother = ["testing"]\n', encoding="utf-8"
    )
    result = phil(calc_repo, "stages", "layout", "-")
    assert result.exit_code == 0, result.output
    assert "Cleared the stages override for layout." in result.output
    assert config_data()["skills"]["stages"] == {"other": ["testing"]}
    assert config_data()["skills"]["folders"] == ["agents"]


def test_an_unknown_stage_is_an_error_that_lists_the_valid_ones(calc_repo):
    result = phil(calc_repo, "stages", "layout", "design,cooking")
    assert result.exit_code == 1
    assert "Unknown stage cooking. Valid stages: planning, design, implementing, testing, reviewing." in result.output
    assert not global_config_path().exists()


def test_stages_needs_at_least_one_stage(calc_repo):
    result = phil(calc_repo, "stages", "layout", " , ")
    assert result.exit_code == 1
    assert "Name at least one stage" in result.output


def test_a_config_file_that_cant_be_read_is_left_alone(calc_repo):
    global_config_path().write_text("[skills\n", encoding="utf-8")
    result = phil(calc_repo, "off", "layout")
    assert result.exit_code == 1
    assert global_config_path().read_text(encoding="utf-8") == "[skills\n"


def test_repo_on_approves_the_skills_by_digest(calc_repo):
    write_skill(calc_repo / ".agents" / "skills" / "mine", "mine")
    result = phil(calc_repo, "repo", "on")
    assert result.exit_code == 0, result.output
    assert "Phil will use this repo's skills" in result.output
    assert repo_approved(paths_of(calc_repo), calc_repo)
    stored = tomllib.loads(paths_of(calc_repo).skills_approval_path.read_text(encoding="utf-8"))
    assert stored["digest"] == repo_digest(calc_repo)


def test_repo_on_without_skills_approves_nothing(calc_repo):
    result = phil(calc_repo, "repo", "on")
    assert result.exit_code == 0, result.output
    assert "This repo has no skills of its own." in result.output
    assert not paths_of(calc_repo).skills_approval_path.exists()


def test_repo_on_refuses_skills_that_are_too_large(calc_repo, monkeypatch):
    write_skill(calc_repo / ".agents" / "skills" / "mine", "mine")
    monkeypatch.setattr(skills_cli, "repo_digest", lambda root: TOO_LARGE)
    result = phil(calc_repo, "repo", "on")
    assert result.exit_code == 1
    assert "This repo's skill folders are too large to approve (more than 2,000 files or 50 MB)." in result.output
    assert not repo_approved(paths_of(calc_repo), calc_repo)


def test_repo_off_withdraws_approval(calc_repo):
    write_skill(calc_repo / ".agents" / "skills" / "mine", "mine")
    set_repo_approved(paths_of(calc_repo), True, repo_digest(calc_repo))
    result = phil(calc_repo, "repo", "off")
    assert result.exit_code == 0, result.output
    assert "Phil won't use this repo's skills." in result.output
    assert not repo_approved(paths_of(calc_repo), calc_repo)


def test_an_approved_repo_is_listed_as_a_source(calc_repo):
    write_skill(calc_repo / ".agents" / "skills" / "mine", "mine")
    phil(calc_repo, "repo", "on")
    result = phil(calc_repo)
    assert "Skill folders: this repo's skills" in result.output


def test_phil_permissions_lists_the_skill_folders(calc_repo, home, monkeypatch):
    (home / ".agents" / "skills").mkdir(parents=True)
    global_config_path().write_text('[skills]\nfolders = ["agents"]\n', encoding="utf-8")
    from phil.cli import permissions as permissions_cli

    monkeypatch.setattr(permissions_cli, "console", make_console(width=300))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "permissions"])
    assert result.exit_code == 0, result.output
    assert "Skill folders Phil's agents can read:" in result.output
    assert "  ~/.agents/skills" in result.output
