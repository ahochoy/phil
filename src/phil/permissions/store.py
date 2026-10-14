"""Policy files on disk (spec 2026-10-13 §3.1, §3.6): `policy.toml` holds `[[allow]]` and `[[deny]]`
entries, each `{rule, added, from}`, plus a `[repo_rules]` table recording whether the user trusted
the rules a repo's own phil.toml ships.

Reading fails safe: a file that can't be read cleanly switches every saved allow rule off (and the
trusted repo rules), but readable deny rules keep applying, and config rules stay. Writing keeps the
user's comments (tomlkit), holds a lock for the whole read-modify-write, and replaces the file
atomically, so a crash or a second Phil never leaves half a file.
"""

import hashlib
import json
import os
import shlex
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import tomlkit
from tomlkit.exceptions import ParseError, TOMLKitError

from phil import platform
from phil.permissions.rules import Rule, RuleError, parse_rule

KINDS = ("allow", "deny")
# The comment a newly created policy file starts with; `scope` is a repo name or `everywhere`.
POLICY_HEADER = "# Phil permissions — {scope}. `phil permissions` shows what's in effect."


class PolicyWriteError(Exception):
    pass


@dataclass(frozen=True)
class PolicyEntry:
    """One rule in effect. `scope` is `everywhere`, `<repo name>`, `<repo name> phil.toml` or
    `config`. A `config` entry is a `[shell] allow` glob pattern, which keeps the old glob matching:
    its `rule` is `Rule(text=<pattern>, words=(), open_end=False)`, never parsed, so the matcher
    must read `rule.text` as the pattern and ignore `words`."""

    rule: Rule
    kind: Literal["allow", "deny"]
    scope: str
    source: str


@dataclass(frozen=True)
class PolicyProblem:
    path: Path
    message: str
    line: int | None


@dataclass(frozen=True)
class RepoShellSettings:
    """The `[shell]` values a repo's own phil.toml sets that need the user's trust (R14): `allow`,
    `pass_env` and `bash`. Until trusted, none of them applies; the trust prompt (Task 6) shows
    them and hashes them. Empty when phil.toml sets none, or can't be read."""

    allow: tuple[str, ...] = ()
    pass_env: tuple[str, ...] = ()
    bash: str | None = None

    @property
    def empty(self) -> bool:
        """Whether phil.toml sets none of them: then there is nothing to trust."""
        return not self.allow and not self.pass_env and self.bash is None


@dataclass(frozen=True)
class PolicySet:
    """`repo_rules_untrusted` is the repo's allow list while no decision covers its settings.
    `repo_decision` is the decision recorded for the repo's current settings (None: none, or the
    repo sets nothing); `repo_trusted` is whether they apply, which a problem in either policy
    file switches off as it does the saved allow rules."""

    deny: tuple[PolicyEntry, ...]
    allow: tuple[PolicyEntry, ...]
    problems: tuple[PolicyProblem, ...]
    repo_rules_untrusted: tuple[str, ...]
    repo_shell: RepoShellSettings = RepoShellSettings()
    repo_decision: str | None = None
    repo_trusted: bool = False


@dataclass
class _FileState:
    doc: Any
    allow: list[PolicyEntry]
    deny: list[PolicyEntry]
    problems: list[PolicyProblem]


def repo_rules_hash(rules: Sequence[str], pass_env: Sequence[str] = (), bash: str | None = None) -> str:
    """Over everything a repo's phil.toml asks the user to trust (R14), as the JSON object
    `{"allow": [...], "pass_env": [...], "bash": ...}`, so no text can fake a boundary between
    two values. A decision recorded under the older allow-only hash simply asks again."""
    encoded = json.dumps({"allow": list(rules), "pass_env": list(pass_env), "bash": bash},
                         ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def repo_shell_hash(settings: RepoShellSettings) -> str:
    """The hash a decision about `settings` is recorded under."""
    return repo_rules_hash(settings.allow, settings.pass_env, settings.bash)


def _normal(rule: Rule) -> str:
    """The rule with its spacing normalised: the words re-joined, plus ` *` when open-ended."""
    return shlex.join(rule.words) + (" *" if rule.open_end else "")


def normalised_rule(text: str) -> str:
    """The text `add_rule` stores for `text`: what the callout shows before saving it (R15).
    Raises RuleError when `text` isn't a valid rule."""
    return _normal(parse_rule(text))


def _same_rule(stored: object, normal: str) -> bool:
    """Whether a rule already in a file (any spacing) is the rule whose normalised text is `normal`."""
    if not isinstance(stored, str):
        return False
    try:
        return _normal(parse_rule(stored)) == normal
    except RuleError:
        return stored.strip() == normal


def _line_of(text: str, needle: str) -> int | None:
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return number
    return None


def _read_file(path: Path, scope: str) -> _FileState:
    """Parse one policy file. A missing or empty file is valid and empty. Every problem found is
    collected; entries that are individually fine are still returned (the caller decides what to
    switch off)."""
    state = _FileState(tomlkit.document(), [], [], [])
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return state
    except (OSError, UnicodeDecodeError) as exc:
        state.problems.append(PolicyProblem(path, f"can't read {path.name}: {exc}", None))
        return state
    try:
        state.doc = tomlkit.parse(text)
    except ParseError as exc:
        state.problems.append(PolicyProblem(path, f"can't parse {path.name}: {exc}", exc.line))
        return state
    except TOMLKitError as exc:
        state.problems.append(PolicyProblem(path, f"can't parse {path.name}: {exc}", None))
        return state
    doc = state.doc
    for kind in KINDS:
        if kind not in doc:
            continue
        items = doc[kind]
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            state.problems.append(PolicyProblem(path, f"`{kind}` must be an array of tables ([[{kind}]])", None))
            continue
        for item in items:
            rule_text = item.get("rule")
            if not isinstance(rule_text, str):
                state.problems.append(PolicyProblem(path, f"a [[{kind}]] entry needs a `rule` string", None))
                continue
            try:
                rule = parse_rule(rule_text)
            except RuleError as exc:
                state.problems.append(PolicyProblem(path, str(exc), _line_of(text, rule_text)))
                continue
            source = str(item.get("from", ""))
            (state.allow if kind == "allow" else state.deny).append(PolicyEntry(rule, kind, scope, source))
    repo_rules = doc.get("repo_rules")
    if repo_rules is not None:
        decision = repo_rules.get("decision") if isinstance(repo_rules, dict) else None
        digest = repo_rules.get("hash") if isinstance(repo_rules, dict) else None
        if decision not in ("trust", "ignore") or not isinstance(digest, str):
            state.problems.append(PolicyProblem(
                path, "[repo_rules] needs decision = \"trust\" or \"ignore\" and a hash string",
                _line_of(text, "[repo_rules]")))
    return state


def _recorded_decision(state: _FileState, rules_hash: str) -> str | None:
    repo_rules = state.doc.get("repo_rules")
    if isinstance(repo_rules, dict) and repo_rules.get("hash") == rules_hash:
        decision = repo_rules.get("decision")
        if decision in ("trust", "ignore"):
            return str(decision)
    return None


def _config_entries(patterns: list[str]) -> list[PolicyEntry]:
    return [PolicyEntry(Rule(text=pattern, words=(), open_end=False), "allow", "config", "config")
            for pattern in patterns]


def config_policy(patterns: list[str]) -> PolicySet:
    """A policy of `[shell] allow` patterns only, with no policy files: what `ShellPolicy([...])`
    uses, and what applies when the rules can't be loaded at all."""
    return PolicySet(deny=(), allow=tuple(_config_entries(patterns)), problems=(), repo_rules_untrusted=())


def load_policy(*, repo_name: str, project_policy: Path, global_policy: Path, config_allow: list[str],
                repo_allow: Sequence[str] = (), repo_shell: RepoShellSettings | None = None) -> PolicySet:
    """Order: deny everywhere, then the repo; allow everywhere, then the repo, then the trusted
    rules the repo's phil.toml ships, then config. Any problem in either file drops every saved allow
    rule and the trusted repo rules (spec §3.6); readable deny rules and config rules stay.

    `repo_shell` is everything the repo's phil.toml sets that needs trust (R14); `repo_allow` is
    short for a repo that sets only `[shell] allow`. The decision is looked up by their hash."""
    everywhere = _read_file(global_policy, "everywhere")
    project = _read_file(project_policy, repo_name)
    problems = tuple(everywhere.problems + project.problems)
    settings = repo_shell if repo_shell is not None else RepoShellSettings(allow=tuple(repo_allow))
    repo_allow = settings.allow

    untrusted: tuple[str, ...] = ()
    repo_entries: list[PolicyEntry] = []
    decision = None if settings.empty else _recorded_decision(project, repo_shell_hash(settings))
    if repo_allow:
        if decision is None:
            untrusted = tuple(repo_allow)
        elif decision == "trust":
            for text in repo_allow:
                try:
                    repo_entries.append(PolicyEntry(parse_rule(text), "allow", f"{repo_name} phil.toml", "phil.toml"))
                except RuleError as exc:
                    problems += (PolicyProblem(project_policy, f"in {repo_name}'s phil.toml: {exc}", None),)

    config = _config_entries(config_allow)
    broken = bool(everywhere.problems or project.problems)
    saved_allow = [] if broken else [*everywhere.allow, *project.allow, *repo_entries]
    return PolicySet(
        deny=tuple(everywhere.deny + project.deny),
        allow=tuple(saved_allow + config),
        problems=problems,
        repo_rules_untrusted=untrusted,
        repo_shell=settings,
        repo_decision=decision,
        repo_trusted=decision == "trust" and not broken,
    )


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on `<path>.lock` for a whole read-modify-write. Each call opens its
    own handle, and both flock (POSIX) and LockFile (Windows) conflict between handles, even in one
    process. The lock file always holds a byte, because msvcrt locks a byte range."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a+b") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            try:
                handle.write(b"\0")
                handle.flush()
            except OSError:  # another writer got there first and holds the byte locked
                pass
        platform.lock_file(handle)
        try:
            yield
        finally:
            platform.unlock_file(handle)


def _open_for_write(path: Path, header: str | None = None) -> Any:
    """The parsed document to modify, or a fresh one; refuses a file that has any problem."""
    state = _read_file(path, "")
    if state.problems:
        first = state.problems[0]
        where = f" (line {first.line})" if first.line else ""
        raise PolicyWriteError(f"{path} has a problem, so Phil won't change it{where}: {first.message}")
    doc = state.doc
    if not path.exists() and header:
        doc.add(tomlkit.comment(header.removeprefix("#").strip()))
        doc.add(tomlkit.nl())
    return doc


def _write(path: Path, doc: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        raise PolicyWriteError(f"can't write {path}: {exc}") from exc


def add_rule(path: Path, kind: str, rule_text: str, source: str, *, today: date, header: str | None = None) -> None:
    """Append `rule_text` to `path` as `{rule, added, from}`. `header` is the comment a newly
    created file starts with. Adding a rule already there (same kind and text) changes nothing."""
    if kind not in KINDS:
        raise PolicyWriteError(f"unknown rule kind {kind!r}")
    try:
        rule = parse_rule(rule_text)
    except RuleError as exc:
        raise PolicyWriteError(str(exc)) from exc
    with _locked(path):
        doc = _open_for_write(path, header)
        normal = _normal(rule)
        if kind in doc:
            if any(_same_rule(item.get("rule"), normal) for item in doc[kind]):
                return
        else:
            if len(doc):
                doc.add(tomlkit.nl())
            doc.add(kind, tomlkit.aot())
        entry = tomlkit.table()
        entry.add("rule", normal)
        entry.add("added", today)
        entry.add("from", source)
        items = doc[kind]
        if len(items) and not items[-1].as_string().endswith("\n\n"):
            items[-1].add(tomlkit.nl())  # tomlkit puts no blank line between appended tables
        items.append(entry)
        _write(path, doc)


def remove_rule(path: Path, rule_text: str) -> bool:
    """Remove the entries (allow or deny) whose rule is `rule_text`. False if there were none."""
    try:
        wanted = _normal(parse_rule(rule_text))
    except RuleError:
        wanted = rule_text.strip()
    if not path.exists():
        return False
    with _locked(path):
        doc = _open_for_write(path)
        removed = False
        for kind in KINDS:
            if kind not in doc:
                continue
            items = doc[kind]
            for index in reversed(range(len(items))):
                if _same_rule(items[index].get("rule"), wanted):
                    del items[index]
                    removed = True
            if not len(items):
                del doc[kind]
        if removed:
            _write(path, doc)
        return removed


def record_repo_decision(path: Path, decision: Literal["trust", "ignore"], rules_hash: str) -> None:
    with _locked(path):
        doc = _open_for_write(path)
        table = tomlkit.table()
        table.add("decision", decision)
        table.add("hash", rules_hash)
        if "repo_rules" in doc:
            del doc["repo_rules"]
        doc.add("repo_rules", table)
        _write(path, doc)


def file_problems(path: Path) -> tuple[PolicyProblem, ...]:
    """What's wrong with one policy file; empty when it's fine or missing. Any problem switches
    off every saved allow rule, and Phil won't write to the file until it's fixed."""
    return tuple(_read_file(path, "").problems)


def repo_decision(path: Path, rules_hash: str) -> str | None:
    """`trust` or `ignore` if the user decided about exactly these rules, else None."""
    return _recorded_decision(_read_file(path, ""), rules_hash)
