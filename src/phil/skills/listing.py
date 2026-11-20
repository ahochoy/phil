"""What `phil skills` and the chat's `/skills` print, and the skill-folders section of `phil permissions`.
Display text only: skill names come from files on disk, so each is cleaned before it is shown."""

from collections.abc import Sequence
from pathlib import Path

from phil.config import PhilConfig
from phil.skills.discover import discover
from phil.skills.labels import stages_for
from phil.skills.runtime import SkillRuntime
from phil.skills.sources import USER_FOLDERS, approved_sources, repo_source, tilde_path
from phil.store.paths import ProjectPaths
from phil.text import printable

NOT_APPROVED = "No skill folders are approved. Run phil setup to choose them."
REPO_HINT = "This repo has its own skills; turn them on with: phil skills repo on"
FOLDERS_HEADER = "Skill folders Phil's agents can read:"
REPO_LABEL = "this repo's skills"


REPO_NOT_APPROVED_HEADER = "This repo's own skills (not approved):"
REPO_APPROVED_HEADER = "Approved this repo's skills:"
SHADOWED_NOTE = "(hidden: you have your own skill with this name)"


def repo_skill_names(repo_root: Path) -> list[str]:
    """The names of the readable skills the repo ships, sorted."""
    source = repo_source(repo_root)
    if source is None:
        return []
    return sorted(skill.name for skill in discover([source]).skills)


def repo_has_skills(repo_root: Path) -> bool:
    """True when the repo ships at least one readable skill of its own."""
    return bool(repo_skill_names(repo_root))


def approved_repo_lines(config: PhilConfig, repo_root: Path) -> list[str]:
    """What `phil skills repo on` approved: each of the repo's skills, marked when one of the user's own
    skills has the same name and so is the one agents get."""
    repo = repo_source(repo_root)
    if repo is None:
        return []
    catalog = discover([*approved_sources(config, None, None), repo])
    users = {skill.name for skill in catalog.skills if skill.source != repo.key}
    entries = [(skill.name, "") for skill in catalog.skills if skill.source == repo.key]
    entries += [(skill.name, f" {SHADOWED_NOTE}") for skill in catalog.hidden if skill.source == repo.key and skill.name in users]
    if not entries:
        return []
    return [REPO_APPROVED_HEADER, *(f"  {printable(name)}{note}" for name, note in sorted(entries))]


def _source_labels(config: PhilConfig, repo_is_approved: bool) -> list[str]:
    # The folder where there is one (plugins have many): this line names folders, not agents.
    labels = [USER_FOLDERS.get(source.key) or source.label for source in approved_sources(config, None, None)]
    if repo_is_approved:
        labels.append(REPO_LABEL)
    return labels


def skill_lines(
    runtime: SkillRuntime | None, config: PhilConfig, repo_names: Sequence[str], repo_approved: bool
) -> list[str]:
    """The approved folders; each skill an agent can be offered with its stages and where they came
    from; then what is off, hidden, not yet labelled or unreadable; then, if the repo ships skills
    (`repo_names`) that aren't approved, their names and how to turn them on."""
    labels = _source_labels(config, repo_approved)
    hint: list[str] = []
    if repo_names and not repo_approved:
        hint = [REPO_NOT_APPROVED_HEADER, *(f"  {printable(name)}" for name in repo_names), REPO_HINT]
    if not labels:
        return [NOT_APPROVED, *hint]
    lines = [f"Skill folders: {', '.join(printable(label) for label in labels)}"]
    off = set(config.skills.off)
    if runtime is not None:
        unlabelled = 0
        for skill in runtime.catalog.skills:
            if skill.name in off:
                continue
            stages, origin = stages_for(skill, runtime.cache, config)
            if stages is None:
                unlabelled += 1
            elif stages:
                lines.append(f"{printable(skill.name)}  {', '.join(stages)} ({origin})")
    if config.skills.off:
        lines.append(f"Off: {', '.join(printable(name) for name in config.skills.off)}")
    if runtime is not None:
        if runtime.catalog.hidden:
            lines.append(f"Hidden duplicates: {', '.join(printable(skill.name) for skill in runtime.catalog.hidden)}")
        if unlabelled:
            lines.append(f"Not labelled yet: {unlabelled}")
        if runtime.catalog.unreadable:
            lines.append(f"Couldn't read: {runtime.catalog.unreadable}")
    return [*lines, *hint]


def skill_folder_lines(config: PhilConfig, repo_root: Path | None, paths: ProjectPaths | None) -> list[str]:
    """`phil permissions`' section of the folders agents can read skills from: one line per approved
    folder. Empty when none is approved."""
    folders = [
        tilde_path(directory) for source in approved_sources(config, repo_root, paths) for directory in source.dirs
    ]
    if not folders:
        return []
    return [FOLDERS_HEADER, *(f"  {printable(folder)}" for folder in folders)]
