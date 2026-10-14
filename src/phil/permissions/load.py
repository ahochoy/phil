"""The permission rules in effect for a repo (spec 2026-10-13 §3.1, §3.5, §3.6): both policy files,
`[shell] allow` from config (defaults and `~/.phil/config.toml`, never the repo's phil.toml), and the
repo's own `[shell] allow`, which applies only once the user trusts it.

A repo's `[shell] pass_env` and `bash` need trust too (R14): `trusted_shell` gives the shell
settings with those, and `allow`, taken from the layers below the repo until the user trusts them.
One decision, recorded under one hash, covers all three.

Loading never drops a deny rule: a phil.toml or config file that can't be read here only empties
the repo's list (and falls back to the defaults for config's), and is reported as a problem."""

import dataclasses
import logging
import tomllib
from pathlib import Path

from phil.config import REPO_SOURCE, PhilConfig, ShellConfig, load_config
from phil.permissions.catalog import catalog_reads, label_for
from phil.permissions.rules import RuleError, parse_rule
from phil.permissions.store import (
    PolicyProblem,
    PolicySet,
    RepoShellSettings,
    config_policy,
    load_policy,
    repo_decision,
    repo_shell_hash,
)
from phil.store.paths import ProjectPaths, global_policy_path

logger = logging.getLogger(__name__)

# The [shell] keys a repo's phil.toml can't set without the user's trust (R14).
UNTRUSTED_SHELL_KEYS = ("allow", "pass_env", "bash")


def _repo_set(config: PhilConfig, key: str) -> bool:
    return config.sources.get(f"shell.{key}") == REPO_SOURCE


def trusted_shell(config: PhilConfig, repo_root: Path | None = None, policy: PolicySet | None = None) -> ShellConfig:
    """`config.shell`, with every value in UNTRUSTED_SHELL_KEYS that the repo's phil.toml set taken
    from the layers below it instead (defaults and `~/.phil/config.toml`). A `--set` value stays.
    If those layers can't be read again, the defaults apply. Never raises.

    When `policy` says the user trusted the repo's settings, its `pass_env` and `bash` apply: the
    values that were hashed, so exactly what the user saw. Its `allow` applies through the policy
    (as `<repo> phil.toml` rules), never as config's glob patterns."""
    keys = [key for key in UNTRUSTED_SHELL_KEYS if _repo_set(config, key)]
    if not keys:
        return config.shell
    trusted: dict[str, object] = {}
    if policy is not None and policy.repo_trusted:
        settings = policy.repo_shell
        if settings.pass_env:
            trusted["pass_env"] = list(settings.pass_env)
        if settings.bash is not None:
            trusted["bash"] = settings.bash
    below_keys = [key for key in keys if key not in trusted]
    below = ShellConfig()
    if below_keys:
        try:
            # Without the repo, load_config never reads repo_root.
            below = load_config(repo_root or Path.cwd(), include_repo=False).shell
        except Exception:
            logger.warning("couldn't read your config again; using the default [shell] settings", exc_info=True)
    update = {key: trusted[key] if key in trusted else getattr(below, key) for key in keys}
    return config.shell.model_copy(update=update)


UNTRUSTED_RULES_NOTICE = (
    "Ignoring {n} rule(s) from phil.toml that you haven't trusted: run `phil permissions trust` to use them"
)
UNTRUSTED_SETTINGS_NOTICE = (
    "Ignoring [shell] {keys} from phil.toml that you haven't trusted: run `phil permissions trust` to use them"
)


def untrusted_notice(policy: PolicySet) -> str | None:
    """The run log's one line when the repo's phil.toml sets things nobody has trusted or ignored
    yet; None when there is nothing to say (an "ignore" decision included)."""
    if policy.repo_rules_untrusted:
        return UNTRUSTED_RULES_NOTICE.format(n=len(policy.repo_rules_untrusted))
    settings = policy.repo_shell
    if settings.empty or policy.repo_decision is not None:
        return None
    keys = [key for key, value in (("pass_env", settings.pass_env), ("bash", settings.bash)) if value]
    return UNTRUSTED_SETTINGS_NOTICE.format(keys=" and ".join(keys)) if keys else None


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


def undecided_repo_shell(repo_root: Path, paths: ProjectPaths) -> tuple[RepoShellSettings, str] | None:
    """The repo's phil.toml shell settings and their hash, when they need the user's answer: set,
    with no decision recorded for this hash (spec §3.5, R14). None otherwise, and when phil.toml
    can't be read (then nothing from it applies, and the policy reports the problem). The hash
    is of exactly the settings returned, so it's the one to record with an answer about them."""
    try:
        settings = repo_shell_settings(repo_root)
    except Exception:
        logger.warning("couldn't read %s's phil.toml to ask about its shell settings", repo_root, exc_info=True)
        return None
    if settings.empty:
        return None
    digest = repo_shell_hash(settings)
    if repo_decision(paths.policy_path, digest) is not None:
        return None
    return settings, digest


def current_repo_shell_hash(repo_root: Path) -> str | None:
    """The hash of the repo's phil.toml shell settings now; None when phil.toml can't be read."""
    try:
        return repo_shell_hash(repo_shell_settings(repo_root))
    except Exception:
        logger.warning("couldn't read %s's phil.toml again", repo_root, exc_info=True)
        return None


def rule_label(text: str) -> str:
    """A repo rule's label in the trust prompt, as the approval callout labels a command."""
    try:
        words = list(parse_rule(text).words)
    except RuleError:
        return "not a valid rule"
    return label_for(words, read_only=catalog_reads(words))


def _config_allow_for(repo_root: Path, config: PhilConfig) -> list[str]:
    """`[shell] allow` from config: defaults, `~/.phil/config.toml` and `--set`, never the repo's
    phil.toml. Raises when the config has to be read again and can't be."""
    if _repo_set(config, "allow"):
        return list(load_config(repo_root, include_repo=False).shell.allow)
    return list(config.shell.allow)


def _load(repo_root: Path, paths: ProjectPaths, config_allow: list[str], repo_shell: RepoShellSettings) -> PolicySet:
    return load_policy(
        repo_name=repo_root.name,
        project_policy=paths.policy_path,
        global_policy=global_policy_path(),
        config_allow=config_allow,
        repo_shell=repo_shell,
    )


def policy_for(repo_root: Path, paths: ProjectPaths, config: PhilConfig) -> PolicySet:
    """The rules in effect. Anything that fails while reading the repo or the config only empties
    the repo's list or falls back to the default [shell] allow, with a problem recorded: the
    policy files, and their deny rules, always load."""
    problems: list[PolicyProblem] = []
    phil_toml = repo_root / "phil.toml"
    try:
        repo_shell = repo_shell_settings(repo_root)
    except Exception as exc:  # RecursionError from a deeply nested file, too
        repo_shell = RepoShellSettings()  # nothing from the repo is trusted
        problems.append(PolicyProblem(phil_toml, f"can't read phil.toml, so its [shell] allow is ignored: {exc!r}", None))
    try:
        config_allow = _config_allow_for(repo_root, config)
    except Exception as exc:
        config_allow = list(ShellConfig().allow)
        problems.append(PolicyProblem(
            phil_toml, f"can't read your config again, so the default [shell] allow applies: {exc!r}", None))
    policy = _load(repo_root, paths, config_allow, repo_shell)
    return dataclasses.replace(policy, problems=(*policy.problems, *problems))


def policy_or_fallback(repo_root: Path, paths: ProjectPaths, config: PhilConfig) -> PolicySet:
    """`policy_for`, never raising, and never dropping a deny rule it can still read. If
    `policy_for` fails, the policy files are loaded again with no repo list and config's allow
    list only. If even that fails, nothing is allowed by a rule: commands ask instead of running."""
    try:
        return policy_for(repo_root, paths, config)
    except Exception as exc:
        logger.warning("couldn't load the permission rules for %s; loading the policy files alone",
                       repo_root, exc_info=True)
        problem = PolicyProblem(paths.policy_path, f"couldn't load the permission rules: {exc!r}", None)
    try:
        config_allow = [] if _repo_set(config, "allow") else list(config.shell.allow)
        policy = _load(repo_root, paths, config_allow, RepoShellSettings())
        return dataclasses.replace(policy, problems=(*policy.problems, problem))
    except Exception as exc:
        logger.warning("couldn't load the permission rules for %s; no rule allows anything this call",
                       repo_root, exc_info=True)
        last = PolicyProblem(paths.policy_path, f"couldn't load the policy files: {exc!r}", None)
        return dataclasses.replace(config_policy([]), problems=(problem, last))
