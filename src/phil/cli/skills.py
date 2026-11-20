"""`phil skills`: list the skills Phil can offer its agents, turn one on or off, set the stages it helps,
and turn this repo's own skills on or off. Skill settings live in ~/.phil/config.toml; the repo's approval
lives with the repo's other project notes."""

from collections.abc import Callable
from pathlib import Path

import tomlkit
import typer
from rich.text import Text

from phil.config import STAGES, PhilConfig, global_config_path
from phil.repo import RepoError, RepoInfo, resolve_repo
from phil.setup.write import _write_atomically
from phil.skills.listing import repo_has_skills, skill_lines
from phil.skills.runtime import load_runtime
from phil.skills.sources import TOO_LARGE, repo_approved, repo_digest, set_repo_approved
from phil.store.paths import ProjectPaths
from phil.text import printable
from phil.ui.theme import make_console

skills_app = typer.Typer(help="List the skills Phil can use, and choose which ones it does.")
repo_app = typer.Typer(help="Turn this repo's own skills on or off.")
skills_app.add_typer(repo_app, name="repo")
console = make_console()

NO_REPO_SKILLS = "This repo has no skills of its own."
TOO_LARGE_TO_APPROVE = "This repo's skill folders are too large to approve (more than 2,000 files or 50 MB)."


def _say(line: str) -> None:
    console.print(Text(line), soft_wrap=True)


def _fail(line: str) -> None:
    _say(line)
    raise typer.Exit(1)


def _project(ctx: typer.Context) -> tuple[RepoInfo, PhilConfig]:
    from phil.cli import main  # main registers this app, so it's imported here, not at the top

    info = main._resolve(ctx)
    return info, main._load_config(info.root, ctx.obj.get("overrides", []))


def _optional_project(ctx: typer.Context) -> tuple[Path | None, ProjectPaths | None, PhilConfig]:
    """The settings work outside a repository too: then there are no repo skills to look through."""
    from phil.cli import main

    root = main._config_root(ctx)
    config = main._load_config(root, ctx.obj.get("overrides", []))
    try:
        info = resolve_repo(root)
    except RepoError:
        return None, None, config
    return info.root, ProjectPaths(info.slug), config


def _repo_state(info: RepoInfo) -> tuple[bool, bool]:
    paths = ProjectPaths(info.slug)
    has_skills = repo_has_skills(info.root)
    return has_skills, has_skills and repo_approved(paths, info.root)


@skills_app.callback(invoke_without_command=True)
def listing(ctx: typer.Context) -> None:
    """List the approved skill folders and the skills in them."""
    if ctx.invoked_subcommand is not None:
        return
    info, config = _project(ctx)
    paths = ProjectPaths(info.slug)
    runtime = load_runtime(config, info.root, paths)
    has_skills, approved = _repo_state(info)
    for line in skill_lines(runtime, config, has_skills, approved):
        _say(line)


def _edit_config(change: Callable[[tomlkit.items.Table], None]) -> None:
    """Apply `change` to the [skills] table of ~/.phil/config.toml, keeping everything else in the file."""
    path = global_config_path()
    try:
        document = tomlkit.parse(path.read_text(encoding="utf-8")) if path.is_file() else tomlkit.document()
    except (OSError, UnicodeDecodeError, tomlkit.exceptions.TOMLKitError) as exc:
        _fail(f"Can't read {path}: {printable(exc)}. Fix it by hand, then try again.")
    table = document.get("skills")
    if table is None:
        table = tomlkit.table()
        document["skills"] = table
    elif not isinstance(table, dict):
        _fail(f"{path} has a [skills] setting that isn't a table. Fix it by hand, then try again.")
    change(table)
    try:
        _write_atomically(path, tomlkit.dumps(document))
    except OSError as exc:
        _fail(f"Can't write {path}: {printable(exc)}")


def _note_if_unknown(ctx: typer.Context, name: str) -> None:
    """The setting is saved either way: the skill may be installed later."""
    root, paths, config = _optional_project(ctx)
    runtime = load_runtime(config, root, paths)
    known = set()
    if runtime is not None:
        known = {skill.name for skill in [*runtime.catalog.skills, *runtime.catalog.hidden]}
    if name not in known:
        _say(f"Phil hasn't found a skill named {printable(name)}; the setting is saved anyway.")


def _set_off(ctx: typer.Context, name: str, off: bool) -> None:
    def change(table) -> None:
        current = [str(entry) for entry in table.get("off", [])]
        wanted = current + [name] if off and name not in current else current
        if not off:
            wanted = [entry for entry in current if entry != name]
        table["off"] = wanted

    _edit_config(change)
    _say(f"Turned {'off' if off else 'on'} {printable(name)}.")
    _note_if_unknown(ctx, name)


@skills_app.command("off")
def turn_off(ctx: typer.Context, name: str = typer.Argument(..., help="The skill's name.")) -> None:
    """Stop Phil offering a skill to its agents."""
    _set_off(ctx, name, True)


@skills_app.command("on")
def turn_on(ctx: typer.Context, name: str = typer.Argument(..., help="The skill's name.")) -> None:
    """Offer a skill to Phil's agents again."""
    _set_off(ctx, name, False)


def _parse_stages(text: str) -> list[str]:
    stages: list[str] = []
    for part in text.split(","):
        stage = part.strip()
        if not stage:
            continue
        if stage not in STAGES:
            _fail(f"Unknown stage {printable(stage)}. Valid stages: {', '.join(STAGES)}.")
        if stage not in stages:
            stages.append(stage)
    if not stages:
        _fail(f"Name at least one stage ({', '.join(STAGES)}), or - to remove the override.")
    return stages


@skills_app.command("stages")
def stages(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="The skill's name."),
    stage_list: str = typer.Argument(..., metavar="STAGES", help=f"Comma-separated, from {', '.join(STAGES)}; - removes the override."),
) -> None:
    """Choose the stages a skill helps, instead of the ones Phil labelled it with."""
    clearing = stage_list.strip() == "-"
    wanted = [] if clearing else _parse_stages(stage_list)

    def change(table) -> None:
        overrides = table.get("stages")
        if clearing:
            if overrides is not None and name in overrides:
                del overrides[name]
            if overrides is not None and not overrides:
                del table["stages"]
            return
        if overrides is None:
            overrides = tomlkit.table()
            table["stages"] = overrides
        overrides[name] = wanted

    _edit_config(change)
    if clearing:
        _say(f"Cleared the stages override for {printable(name)}.")
    else:
        _say(f"{printable(name)} now helps these stages: {', '.join(wanted)}.")
    _note_if_unknown(ctx, name)


@repo_app.command("on")
def repo_on(ctx: typer.Context) -> None:
    """Let Phil use the skills this repo ships. Approval ends if they change."""
    info, _ = _project(ctx)
    if not repo_has_skills(info.root):
        _say(NO_REPO_SKILLS)
        return
    digest = repo_digest(info.root)
    if digest == TOO_LARGE:
        _fail(TOO_LARGE_TO_APPROVE)
    try:
        set_repo_approved(ProjectPaths(info.slug), True, digest)
    except OSError as exc:
        _fail(f"Can't save the approval: {printable(exc)}")
    _say("Phil will use this repo's skills. If they change, you'll need to run this again.")


@repo_app.command("off")
def repo_off(ctx: typer.Context) -> None:
    """Stop Phil using the skills this repo ships."""
    info, _ = _project(ctx)
    try:
        set_repo_approved(ProjectPaths(info.slug), False)
    except OSError as exc:
        _fail(f"Can't save the setting: {printable(exc)}")
    _say("Phil won't use this repo's skills.")
