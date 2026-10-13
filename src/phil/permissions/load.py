"""The permission rules in effect for a repo (spec 2026-10-13 §3.1, §3.5, §3.6): both policy files,
`[shell] allow` from config (defaults and `~/.phil/config.toml`, never the repo's phil.toml), and the
repo's own `[shell] allow`, which applies only once the user trusts it.

A repo's `[shell] pass_env` and `bash` need trust too (R14): `trusted_shell` gives the shell
settings with those, and `allow`, taken from the layers below the repo.

Loading never drops a deny rule: a phil.toml or config file that can't be read here only empties
the repo's list (and falls back to the defaults for config's), and is reported as a problem."""

import dataclasses
import logging
import tomllib
from pathlib import Path

from phil.config import REPO_SOURCE, PhilConfig, ShellConfig, load_config
from phil.permissions.store import PolicyProblem, PolicySet, RepoShellSettings, config_policy, load_policy
from phil.store.paths import ProjectPaths, global_policy_path

logger = logging.getLogger(__name__)

# The [shell] keys a repo's phil.toml can't set without the user's trust (R14).
UNTRUSTED_SHELL_KEYS = ("allow", "pass_env", "bash")


def _repo_set(config: PhilConfig, key: str) -> bool:
    return config.sources.get(f"shell.{key}") == REPO_SOURCE


def trusted_shell(config: PhilConfig, repo_root: Path | None = None) -> ShellConfig:
    """`config.shell`, with every value in UNTRUSTED_SHELL_KEYS that the repo's phil.toml set taken
    from the layers below it instead (defaults and `~/.phil/config.toml`). A `--set` value stays.
    If those layers can't be read again, the defaults apply. Never raises."""
    keys = [key for key in UNTRUSTED_SHELL_KEYS if _repo_set(config, key)]
    if not keys:
        return config.shell
    try:
        # Without the repo, load_config never reads repo_root.
        below = load_config(repo_root or Path.cwd(), include_repo=False).shell
    except Exception:
        logger.warning("couldn't read your config again; using the default [shell] settings", exc_info=True)
        below = ShellConfig()
    return config.shell.model_copy(update={key: getattr(below, key) for key in keys})


def repo_shell_settings(repo_root: Path) -> RepoShellSettings:
    """The untrusted [shell] values the repo's own phil.toml sets. Raises OSError,
    UnicodeDecodeError or TOMLDecodeError when phil.toml can't be read."""
    try:
        text = (repo_root / "phil.toml").read_text(encoding="utf-8")
    except FileNotFoundError:
        return RepoShellSettings()
    shell = tomllib.loads(text).get("shell")
    if not isinstance(shell, dict):
        return RepoShellSettings()

    def strings(value: object) -> tuple[str, ...]:
        return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()

    bash = shell.get("bash")
    return RepoShellSettings(strings(shell.get("allow")), strings(shell.get("pass_env")),
                             bash if isinstance(bash, str) else None)


def policy_for(repo_root: Path, paths: ProjectPaths, config: PhilConfig) -> PolicySet:
    problems: list[PolicyProblem] = []
    phil_toml = repo_root / "phil.toml"
    try:
        repo_shell = repo_shell_settings(repo_root)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        repo_shell = RepoShellSettings()  # nothing from the repo is trusted
        problems.append(PolicyProblem(phil_toml, f"can't read phil.toml, so its [shell] allow is ignored: {exc}", None))
    if _repo_set(config, "allow"):
        try:
            config_allow = list(load_config(repo_root, include_repo=False).shell.allow)
        except Exception as exc:
            config_allow = list(ShellConfig().allow)
            problems.append(PolicyProblem(
                phil_toml, f"can't read your config again, so the default [shell] allow applies: {exc}", None))
    else:
        config_allow = list(config.shell.allow)
    policy = load_policy(
        repo_name=repo_root.name,
        project_policy=paths.policy_path,
        global_policy=global_policy_path(),
        config_allow=config_allow,
        repo_allow=list(repo_shell.allow),
    )
    return dataclasses.replace(policy, problems=(*policy.problems, *problems), repo_shell=repo_shell)


def policy_or_fallback(repo_root: Path, paths: ProjectPaths, config: PhilConfig) -> PolicySet:
    """`policy_for`, never raising. Loading is fail-safe already, but a failure here must never
    break an agent call or a plan check: then only `[shell] allow` from config applies (none of it
    when the repo's phil.toml set it, since that list needs trust)."""
    try:
        return policy_for(repo_root, paths, config)
    except Exception:
        logger.warning("couldn't load the permission rules for %s; using [shell] allow from config only",
                       repo_root, exc_info=True)
        return config_policy([] if _repo_set(config, "allow") else list(config.shell.allow))
