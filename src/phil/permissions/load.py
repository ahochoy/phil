"""The permission rules in effect for a repo (spec 2026-10-13 §3.1, §3.5, §3.6): both policy files,
`[shell] allow` from config (defaults and `~/.phil/config.toml`, never the repo's phil.toml), and the
repo's own `[shell] allow`, which applies only once the user trusts it."""

import logging
import tomllib
from pathlib import Path

from phil.config import REPO_SOURCE, PhilConfig, load_config
from phil.permissions.store import PolicySet, config_policy, load_policy
from phil.store.paths import ProjectPaths, global_policy_path

logger = logging.getLogger(__name__)


def _repo_set_allow(config: PhilConfig) -> bool:
    return config.sources.get("shell.allow") == REPO_SOURCE


def _config_allow(repo_root: Path, config: PhilConfig) -> list[str]:
    """`[shell] allow` as config sets it. When the repo's phil.toml set it, the layers below it are
    read again without the repo: the repo's list is not config, but a list the user must trust."""
    if _repo_set_allow(config):
        return list(load_config(repo_root, include_repo=False).shell.allow)
    return list(config.shell.allow)


def _repo_allow(repo_root: Path) -> list[str]:
    """`[shell] allow` from the repo's own phil.toml, or [] when it has none."""
    try:
        data = tomllib.loads((repo_root / "phil.toml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    shell = data.get("shell")
    allow = shell.get("allow") if isinstance(shell, dict) else None
    if not isinstance(allow, list):
        return []
    return [item for item in allow if isinstance(item, str)]


def policy_for(repo_root: Path, paths: ProjectPaths, config: PhilConfig) -> PolicySet:
    return load_policy(
        repo_name=repo_root.name,
        project_policy=paths.policy_path,
        global_policy=global_policy_path(),
        config_allow=_config_allow(repo_root, config),
        repo_allow=_repo_allow(repo_root),
    )


def policy_or_fallback(repo_root: Path, paths: ProjectPaths, config: PhilConfig) -> PolicySet:
    """`policy_for`, never raising. Loading is fail-safe already, but a failure here must never
    break an agent call or a plan check: then only `[shell] allow` from config applies (none of it
    when the repo's phil.toml set it, since that list needs trust)."""
    try:
        return policy_for(repo_root, paths, config)
    except Exception:
        logger.warning("couldn't load the permission rules for %s; using [shell] allow from config only",
                       repo_root, exc_info=True)
        return config_policy([] if _repo_set_allow(config) else list(config.shell.allow))
