import hashlib
import json
import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomlkit

from phil.config import PhilConfig
from phil.setup.write import _write_atomically
from phil.store.paths import ProjectPaths

logger = logging.getLogger(__name__)

# (key, folder under the home directory). "claude-plugins" has no fixed folder: see _plugin_dirs.
USER_SOURCES: tuple[tuple[str, str | None], ...] = (
    ("agents", "~/.agents/skills"),
    ("claude", "~/.claude/skills"),
    ("claude-plugins", None),
    ("codex", "~/.codex/skills"),
    ("cursor", "~/.cursor/skills"),
    ("gemini", "~/.gemini/skills"),
)
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
    for key, folder in USER_SOURCES:
        if folder is None:
            dirs = _plugin_dirs()
            label = "Claude plugins"
        else:
            path = Path(folder).expanduser()
            dirs = (path,) if path.is_dir() else ()
            label = folder
        if dirs:
            sources.append(Source(key=key, label=label, dirs=dirs))
    return sources


def repo_source(repo_root: Path) -> Source | None:
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


def repo_digest(repo_root: Path) -> str:
    """A digest of the repo's skills: the sha256 of their sorted (SKILL.md path from the repo root, content hash) pairs."""
    from phil.skills.discover import discover  # discover imports Source from here

    source = repo_source(repo_root)
    root = repo_root.resolve()
    pairs: list[tuple[str, str]] = []
    if source is not None:
        for skill in discover([source]).skills:
            skill_file = skill.path / "SKILL.md"
            try:
                relative = skill_file.relative_to(root).as_posix()
            except ValueError:  # a link out of the repo: use where it really is
                relative = skill_file.as_posix()
            pairs.append((relative, skill.content_hash))
    digest = hashlib.sha256()
    for relative, content_hash in sorted(pairs):
        digest.update(f"{relative}\0{content_hash}\n".encode())
    return digest.hexdigest()


def repo_approved(paths: ProjectPaths, repo_root: Path) -> bool:
    """True when the repo's skills are approved and are still the ones that were approved."""
    document = _read_approval(paths)
    stored = document.get("digest")
    return document.get("approved") is True and isinstance(stored, str) and stored == repo_digest(repo_root)


def set_repo_approved(paths: ProjectPaths, approved: bool, digest: str | None = None) -> None:
    document = _read_approval(paths)
    document["approved"] = approved
    if approved and digest is not None:
        document["digest"] = digest
    elif "digest" in document:
        del document["digest"]
    _write_atomically(paths.skills_approval_path, tomlkit.dumps(document))


def approved_sources(config: PhilConfig, repo_root: Path | None, paths: ProjectPaths | None) -> list[Source]:
    """Each approved user source first, then the repo's skills (when approved and unchanged), so a repo
    cannot replace a skill the user installed."""
    sources: list[Source] = []
    approved = {Path(entry).expanduser() if "/" in entry or "~" in entry else entry for entry in config.skills.folders}
    for key, folder in USER_SOURCES:
        wanted = key in approved or (folder is not None and Path(folder).expanduser() in approved)
        if not wanted:
            continue
        sources.extend(source for source in user_sources() if source.key == key)
    if repo_root is not None and paths is not None and repo_approved(paths, repo_root):
        repo = repo_source(repo_root)
        if repo is not None:
            sources.append(repo)
    return sources
