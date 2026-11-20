import hashlib
import json
import logging
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePath

import tomlkit

from phil.config import PhilConfig
from phil.setup.write import _write_atomically
from phil.store.paths import ProjectPaths

logger = logging.getLogger(__name__)

# (key, label shown in setup, folder under the home directory). "claude-plugins" has no fixed folder:
# see _plugin_dirs.
USER_SOURCES: tuple[tuple[str, str, str | None], ...] = (
    ("agents", "Shared skills (npx skills)", "~/.agents/skills"),
    ("claude", "Claude Code", "~/.claude/skills"),
    ("claude-plugins", "Claude Code plugins", None),
    ("codex", "Codex", "~/.codex/skills"),
    ("cursor", "Cursor", "~/.cursor/skills"),
    ("gemini", "Gemini", "~/.gemini/skills"),
)
USER_FOLDERS: dict[str, str | None] = {key: folder for key, _, folder in USER_SOURCES}
REPO_KEY = "repo"
REPO_DIRS = (".agents/skills", ".claude/skills")
_PLUGIN_LIST = "~/.claude/plugins/installed_plugins.json"


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    dirs: tuple[Path, ...]


def _plugin_dirs() -> tuple[Path, ...]:
    """The `skills` folder of each installed plugin version, as listed in installed_plugins.json."""
    listing = Path(_PLUGIN_LIST).expanduser()
    try:
        data = json.loads(listing.read_text(encoding="utf-8"))
        entries = [entry for versions in data["plugins"].values() for entry in versions]
        install_paths = [Path(entry["installPath"]) for entry in entries]
    except FileNotFoundError:
        return ()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        logger.info("skills: ignoring plugin list %s (%s)", listing, error)
        return ()
    found: list[Path] = []
    for install_path in install_paths:
        skills = install_path / "skills"
        if skills.is_dir() and skills not in found:
            found.append(skills)
    return tuple(found)


def user_sources() -> list[Source]:
    """The user-level skill sources that exist on this machine, in USER_SOURCES order."""
    sources: list[Source] = []
    for key, label, folder in USER_SOURCES:
        if folder is None:
            dirs = _plugin_dirs()
        else:
            path = Path(folder).expanduser()
            dirs = (path,) if path.is_dir() else ()
        if dirs:
            sources.append(Source(key=key, label=label, dirs=dirs))
    return sources


def tilde_path(path: PurePath, home: PurePath | None = None) -> str:
    """`path` as setup and `/permissions` show a skill folder: under the home folder, `~/` and forward
    slashes on every OS (the way the folders are written in config and the docs); otherwise as is."""
    try:
        return "~/" + path.relative_to(Path.home() if home is None else home).as_posix()
    except ValueError:
        return str(path)


def is_link(path: Path) -> bool:
    """True when `path` itself is a symlink (or a Windows junction). Never follows it."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    junction = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    return stat.S_ISLNK(info.st_mode) or (junction is not None and getattr(info, "st_reparse_tag", 0) == junction)


def links_on_the_way(repo_root: Path, skills_dir: Path) -> bool:
    """True when any folder from the repo root down to `skills_dir` (`.agents`, then `.agents/skills`)
    is a link. Checked with lstat only, so nothing is resolved (a link could name a network path)."""
    try:
        parts = skills_dir.relative_to(repo_root).parts
    except ValueError:
        return True
    path = repo_root
    for part in parts:
        path = path / part
        if is_link(path):
            return True
    return False


def repo_has_links(repo_root: Path) -> bool:
    """True when a folder on the way to one of the repo's skill folders is a link."""
    return any(links_on_the_way(repo_root, repo_root / folder) for folder in REPO_DIRS)


def repo_source(repo_root: Path) -> Source | None:
    """The repo's own skill folders, or None when it has none or one is reached through a link (a repo
    could point a link anywhere, so its skill folders must be real folders inside it)."""
    if repo_has_links(repo_root):
        logger.info("skills: %s's skill folders are reached through a link; leaving them out", repo_root)
        return None
    dirs = tuple(repo_root / folder for folder in REPO_DIRS if (repo_root / folder).is_dir())
    if not dirs:
        return None
    return Source(key=REPO_KEY, label="this repo's skills", dirs=dirs)


def _read_approval(paths: ProjectPaths) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(paths.skills_approval_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return tomlkit.document()
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, tomlkit.exceptions.TOMLKitError) as error:
        logger.info("skills: ignoring %s (%s)", paths.skills_approval_path, error)
        return tomlkit.document()


MAX_DIGEST_ENTRIES = 2000
MAX_DIGEST_BYTES = 50 * 1024 * 1024
# The digest of a repo whose skills are too big to check. Approval never matches it.
TOO_LARGE = "too-large"


class _TooLarge(Exception):
    pass


def _file_hash(path: Path, budget: list[int]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise _TooLarge(f"more than {MAX_DIGEST_BYTES} bytes of skill content")
            digest.update(chunk)
    return digest.hexdigest()


def _tree_lines(directory: Path, budget: list[int], counter: list[int]) -> list[str]:
    """One line per entry under `directory`, in sorted order: relative path, type, and content hash or link target."""
    lines: list[str] = []
    pending = [directory]
    while pending:
        current = pending.pop()
        entries = sorted(current.iterdir(), key=lambda entry: entry.name)
        subdirs: list[Path] = []
        for entry in entries:
            counter[0] += 1
            if counter[0] > MAX_DIGEST_ENTRIES:
                raise _TooLarge(f"more than {MAX_DIGEST_ENTRIES} entries in skill folders")
            relative = entry.relative_to(directory).as_posix()
            if entry.is_symlink():
                lines.append(f"{relative}\0symlink\0{os.readlink(entry)}")
            elif entry.is_dir():
                lines.append(f"{relative}\0dir")
                subdirs.append(entry)
            elif entry.is_file():
                lines.append(f"{relative}\0file\0{_file_hash(entry, budget)}")
            else:
                lines.append(f"{relative}\0other")
        pending.extend(reversed(subdirs))
    return sorted(lines)


def repo_digest(repo_root: Path) -> str:
    """A digest of the repo's skills: for each skill, its SKILL.md path from the repo root and every entry
    under its folder (path, type, and file content or link target). TOO_LARGE when there is too much to check."""
    from phil.skills.discover import discover  # discover imports Source from here

    if repo_has_links(repo_root):
        logger.info("skills: repo skills not checked for approval (a skill folder is a link)")
        return TOO_LARGE
    source = repo_source(repo_root)
    root = repo_root.resolve()
    blocks: list[str] = []
    if source is not None:
        budget = [MAX_DIGEST_BYTES]
        counter = [0]
        try:
            for skill in discover([source]).skills:
                skill_file = skill.path / "SKILL.md"
                try:
                    relative = skill_file.relative_to(root).as_posix()
                except ValueError:  # a link out of the repo: use where it really is
                    relative = skill_file.as_posix()
                tree = _tree_lines(skill.path, budget, counter)
                blocks.append(f"{relative}\0{skill.content_hash}\n" + "\n".join(tree))
        except _TooLarge as reason:
            logger.info("skills: repo skills not checked for approval (%s)", reason)
            return TOO_LARGE
        except OSError as error:
            logger.info("skills: repo skills not checked for approval (%s)", error)
            return TOO_LARGE
    digest = hashlib.sha256()
    for block in sorted(blocks):
        digest.update(block.encode("utf-8", "surrogateescape"))
        digest.update(b"\n\n")
    return digest.hexdigest()


def repo_approved(paths: ProjectPaths, repo_root: Path) -> bool:
    """True when the repo's skills are approved and are still the ones that were approved."""
    document = _read_approval(paths)
    stored = document.get("digest")
    if document.get("approved") is not True or not isinstance(stored, str) or stored == TOO_LARGE:
        return False
    return stored == repo_digest(repo_root)


def set_repo_approved(paths: ProjectPaths, approved: bool, digest: str | None = None) -> None:
    document = _read_approval(paths)
    document["approved"] = approved
    if approved and digest is not None:
        document["digest"] = digest
    elif "digest" in document:
        del document["digest"]
    _write_atomically(paths.skills_approval_path, tomlkit.dumps(document))


def repo_mentioned(paths: ProjectPaths) -> str | None:
    """The digest of the repo skills the user was last told about, or None (never told)."""
    mentioned = _read_approval(paths).get("mentioned")
    return mentioned if isinstance(mentioned, str) else None


def set_repo_mentioned(paths: ProjectPaths, digest: str) -> None:
    document = _read_approval(paths)
    document["mentioned"] = digest
    _write_atomically(paths.skills_approval_path, tomlkit.dumps(document))


def approved_sources(config: PhilConfig, repo_root: Path | None, paths: ProjectPaths | None) -> list[Source]:
    """Each approved user source first, then the repo's skills (when approved and unchanged), so a repo
    cannot replace a skill the user installed."""
    sources: list[Source] = []
    approved = {Path(entry).expanduser() if "/" in entry or "~" in entry else entry for entry in config.skills.folders}
    for key, _, folder in USER_SOURCES:
        wanted = key in approved or (folder is not None and Path(folder).expanduser() in approved)
        if not wanted:
            continue
        sources.extend(source for source in user_sources() if source.key == key)
    if repo_root is not None and paths is not None and repo_approved(paths, repo_root):
        repo = repo_source(repo_root)
        if repo is not None:
            sources.append(repo)
    return sources
