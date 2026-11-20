"""What `phil skills` and the chat's `/skills` print, and the skill-folders section of `phil permissions`.
Display text only: skill names come from files on disk, so each is cleaned before it is shown."""

from pathlib import Path

from phil.config import PhilConfig
from phil.permissions.listing import shown_path
from phil.skills.discover import discover
from phil.skills.labels import stages_for
from phil.skills.runtime import SkillRuntime
from phil.skills.sources import approved_sources, repo_source
from phil.store.paths import ProjectPaths
from phil.text import printable

NOT_APPROVED = "No skill folders are approved. Run phil setup to choose them."
REPO_HINT = "This repo has its own skills; turn them on with: phil skills repo on"
FOLDERS_HEADER = "Skill folders Phil's agents can read:"
REPO_LABEL = "this repo's skills"


def repo_has_skills(repo_root: Path) -> bool:
    """True when the repo ships at least one readable skill of its own."""
    source = repo_source(repo_root)
    return source is not None and bool(discover([source]).skills)


def _source_labels(config: PhilConfig, repo_is_approved: bool) -> list[str]:
    labels = [source.label for source in approved_sources(config, None, None)]
    if repo_is_approved:
        labels.append(REPO_LABEL)
    return labels


def skill_lines(runtime: SkillRuntime | None, config: PhilConfig, repo_has_skills: bool, repo_approved: bool) -> list[str]:
    """The approved folders; each skill an agent can be offered with its stages and where they came
    from; then what is off, hidden, not yet labelled or unreadable; then, if the repo ships skills
    that aren't approved, how to turn them on."""
    labels = _source_labels(config, repo_approved)
    hint = [REPO_HINT] if repo_has_skills and not repo_approved else []
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
        shown_path(directory) for source in approved_sources(config, repo_root, paths) for directory in source.dirs
    ]
    if not folders:
        return []
    return [FOLDERS_HEADER, *(f"  {printable(folder)}" for folder in folders)]
