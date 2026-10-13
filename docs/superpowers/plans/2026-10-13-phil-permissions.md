# Permissions (M4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every agent shell command gets one ordered decision: forbidden, deny rule, built-in read-only, allow rule, run approval, or ask. Approvals can be saved for this repo or for everywhere. A repo can't grant itself permissions.

**Architecture:**
- A new package, `src/phil/permissions/`, has three parts:
  - `rules.py`: rule matching, program names and suggested rules. Pure.
  - `catalog.py`: the read-only subcommand list and the labels. Pure.
  - `store.py`: the policy files on disk.
- `ShellPolicy` (`workspace/shell.py`) takes a loaded `PolicySet` and runs the order from spec §3.1.
- The engine's approval escalation offers saving, and writes the rules itself, so the chat and `phil attach` behave the same.
- The chat asks about trusting the repo's `phil.toml` rules.
- `phil permissions` manages the rules.

**Tech Stack:** Python 3.14, tomlkit (already a dependency), typer, pytest.

**Spec:** `docs/superpowers/specs/2026-10-13-phil-permissions-design.md`

**Base:** branch `plan-permissions`, off `main`.

## Global Constraints

- **Editing and committing:**
  - Edit files only with the Edit and Write tools. Never edit through python, perl, sed, heredocs (including empty ones) or printf in Bash.
  - To commit, write the message to a file with Write, then run `git commit -F <file>`.
  - Every commit message ends with a blank line, then exactly `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`, whatever model you are.
- **Safety:**
  - Keep the words "keychain" and "credentials" out of Bash command lines.
  - Never work around a hook or guard. Report BLOCKED instead.
  - Never read or print `.env` files.
- **Tests:**
  - Never run `-m live` or `-m bench`.
  - Iterate with `uv run pytest <paths> -q -n 0`.
  - Run the full `uv run pytest -q` once at the end of each task, which takes about 9 minutes, and wait for it before committing.
  - New tests must be portable, because CI runs Ubuntu, macOS and Windows.
  - Test output stays clean.
- **Text files:** I/O passes `encoding="utf-8"`, and writes also pass `newline="\n"`.
- **Rendering:** agent, command and rule text is plain text, never Rich markup. Escape it, or build it as `Text`.
- **Labels, verbatim:** `reads only`, `changes things`, `may change things`.
- **Reasons, verbatim forms:**

| Situation | Reason |
|---|---|
| A built-in read-only command | `read-only (built-in)` |
| An allow rule | `rule "<rule>" (<scope>)`, where scope is `everywhere`, `<repo name>`, `<repo name> phil.toml` or `config` |
| A deny rule | `deny rule "<rule>" (<scope>)` |
| Approved during the run | `approved for this run` |
| The plan's test or check command | `the plan's test command` |
| Nothing matched | `no rule matches` |
| Forbidden | `forbidden: <detail>` |

- **Feed line after saving, verbatim:** `Saved rule "<rule>" for <repo name>` or `Saved rule "<rule>" for everywhere`. With several rules: `Saved rules "<a>", "<b>" for <scope>`. It is followed by ` · /permissions to review`.
- **Callout options, verbatim:**
  - `Allow for this run`
  - `Always allow in <repo name>: <rules joined by "  ·  ">`
  - `Always allow everywhere: <rules joined by "  ·  ">`
  - `Deny`
  - `Stop the run`
- **Escalation option answers:** `approve`, `always_repo`, `always_everywhere`, `deny` and `abort`.

## Review Focus

1. **A full-path program.** `/usr/bin/python3 -c "…"` and `/usr/bin/git -c x=y log` are forbidden on POSIX, the same as on Windows. Test in Task 1.
2. **A deny rule against a read-only command.** With `[[deny]] rule = "git log *"`, `git log` is denied even though it's on the built-in list, and the decision reports the deny rule. Test in Task 4.
3. **A broken everywhere file plus a valid repo allow rule.** The repo allow rule is not applied (fail safe), the built-in read-only list still works, and saving refuses. Test in Task 3 for the store and Task 4 for the policy.
4. **The repo `phil.toml` list changing after it was trusted.** Its rules stop applying until it's trusted again, and an unattended worker logs the notice. Test in Task 3 for the hash and Task 6 for the prompt.
5. **"Always allow" with an interpreter among the denied commands.** There's no "everywhere" choice, and the interpreter is saved as the exact command. Test in Task 5.

---

### Task 1: Rules, program names and suggested rules (pure)

**Files:**
- Create: `src/phil/permissions/__init__.py` (empty), `src/phil/permissions/rules.py`, `tests/permissions/__init__.py` (empty), `tests/permissions/test_rules.py`
- Modify: `src/phil/workspace/shell.py`, the `_program_name` function (around lines 462–472)

**Interfaces:**
- Produces:
  - `program_name(arg0: str) -> str`
  - `Rule` (frozen dataclass: `text: str`, `words: tuple[str, ...]`, `open_end: bool`)
  - `parse_rule(text: str) -> Rule`, which raises `RuleError(ValueError)`
  - `rule_matches(rule: Rule, argv: list[str]) -> bool`
  - `suggest_rule(argv: list[str]) -> str`
  - `exact_only(argv: list[str]) -> bool`
  - `is_bare_program(rule: Rule) -> bool`
  - The constants `INTERPRETERS`, `DESTRUCTIVE` and `SUBCOMMAND_DEPTH`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/permissions/test_rules.py
import pytest

from phil import platform
from phil.permissions.rules import (
    RuleError, exact_only, is_bare_program, parse_rule, program_name, rule_matches, suggest_rule,
)
from phil.workspace.shell import ShellPolicy


@pytest.mark.parametrize("arg0,name", [
    ("gh", "gh"), ("/usr/bin/gh", "gh"), ("/usr/bin/python3", "python3"), ("./tools/run", "run"),
])
def test_program_name_is_the_basename(arg0, name):
    assert program_name(arg0) == name


def test_program_name_windows_rules(monkeypatch):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    assert program_name(r"C:\x\Python3.EXE") == "python3"
    assert program_name("C:/x/npm.cmd") == "npm"


@pytest.mark.parametrize("command", ["/usr/bin/python3 -c print(1)", "/usr/bin/git -c core.pager=x log",
                                     "/opt/homebrew/bin/uv --prefix=/tmp pip list"])
def test_full_path_risky_programs_are_forbidden_on_posix(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


def test_rule_words_and_open_end():
    rule = parse_rule("gh pr checks *")
    assert rule.words == ("gh", "pr", "checks") and rule.open_end
    assert rule_matches(rule, ["gh", "pr", "checks"])
    assert rule_matches(rule, ["/usr/local/bin/gh", "pr", "checks", "42", "--watch"])
    assert not rule_matches(rule, ["gh", "pr", "merge", "42"])
    exact = parse_rule("make test")
    assert rule_matches(exact, ["make", "test"]) and not rule_matches(exact, ["make", "test", "-j4"])


@pytest.mark.parametrize("text", ["", "*", "gh * view", "gh pr*", "gh 'unclosed"])
def test_invalid_rules(text):
    with pytest.raises(RuleError):
        parse_rule(text)


def test_bare_program():
    assert is_bare_program(parse_rule("gh *")) and not is_bare_program(parse_rule("gh pr *"))


@pytest.mark.parametrize("argv,rule", [
    (["gh", "pr", "checks", "42"], "gh pr checks *"),
    (["git", "push", "origin", "main"], "git push *"),
    (["npm", "install", "left-pad"], "npm install *"),
    (["uv", "pip", "install", "x"], "uv pip install *"),
    (["docker", "compose", "up", "-d"], "docker compose up *"),
    (["make", "test-unit", "--fast"], "make test-unit *"),
    (["make", "-j4", "test"], "make -j4 test *"),
    (["ruff"], "ruff"),
    (["ruff", "--fix"], "ruff --fix"),
    (["python3", "scripts/x.py"], "python3 scripts/x.py"),
    (["rm", "build.log"], "rm build.log"),
    (["git", "push", "--force", "origin"], "git push --force origin"),
    (["gh", "--version"], "gh --version"),
])
def test_suggest_rule(argv, rule):
    assert suggest_rule(argv) == rule


def test_suggestion_never_bare_program():
    for argv in (["gh"], ["gh", "--help"], ["make"], ["cargo", "-q"]):
        assert not is_bare_program(parse_rule(suggest_rule(argv)))


@pytest.mark.parametrize("argv,expected", [
    (["python3", "x.py"], True), (["/usr/bin/node", "x.js"], True), (["bash", "x.sh"], True),
    (["rm", "-rf", "build"], True), (["kill", "12"], True), (["git", "push", "-f"], True),
    (["git", "push", "origin"], False), (["gh", "pr", "merge", "1"], False), (["make", "test"], False),
])
def test_exact_only(argv, expected):
    assert exact_only(argv) is expected
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/permissions/test_rules.py -q -n 0`. Expected: an ImportError.

- [ ] **Step 3: Implement**

```python
# src/phil/permissions/rules.py
"""Permission rules (spec 2026-10-13 §3.1, §3.3): a rule is a command as words, compared by program
name, with an optional trailing `*` meaning "any further arguments, or none"."""

import re
import shlex
from dataclasses import dataclass

from phil import platform

INTERPRETERS = frozenset({"python", "python3", "node", "bash", "sh", "zsh", "ruby", "perl"})
DESTRUCTIVE = frozenset({"rm", "mv", "chmod", "chown", "dd", "kill"})
# How many leading subcommand words a suggestion keeps, per program; a (program, first word) entry
# overrides the program's depth for that subcommand family.
SUBCOMMAND_DEPTH: dict[str | tuple[str, str], int] = {
    "gh": 2, "git": 1, "npm": 1, "pnpm": 1, "yarn": 1, "uv": 1, "cargo": 1, "go": 1,
    "docker": 1, "kubectl": 1,
    ("uv", "pip"): 2, ("uv", "python"): 2, ("go", "mod"): 2, ("docker", "compose"): 2,
}


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    text: str
    words: tuple[str, ...]
    open_end: bool


def program_name(arg0: str) -> str:
    """The program `arg0` names: its basename, so `/usr/bin/gh` is `gh`. On Windows either
    separator splits, and the name is lowercased without a .exe/.cmd/.bat suffix."""
    if platform.IS_WINDOWS:
        name = re.split(r"[\\/]", arg0)[-1].lower()
        for suffix in (".exe", ".cmd", ".bat"):
            if name.endswith(suffix):
                return name.removesuffix(suffix)
        return name
    return arg0.rsplit("/", 1)[-1]


def parse_rule(text: str) -> Rule:
    try:
        words = shlex.split(text)
    except ValueError as exc:
        raise RuleError(f"can't read rule {text!r}: {exc}") from exc
    open_end = bool(words) and words[-1] == "*"
    fixed = words[:-1] if open_end else words
    if not fixed:
        raise RuleError(f"rule {text!r} names no program")
    if any("*" in word for word in fixed):
        raise RuleError(f"rule {text!r}: `*` is only allowed as the last word")
    return Rule(text=text.strip(), words=(program_name(fixed[0]), *fixed[1:]), open_end=open_end)


def rule_matches(rule: Rule, argv: list[str]) -> bool:
    if not argv or program_name(argv[0]) != rule.words[0]:
        return False
    fixed, rest = rule.words[1:], argv[1:]
    if rule.open_end:
        return len(rest) >= len(fixed) and tuple(rest[: len(fixed)]) == fixed
    return tuple(rest) == fixed


def is_bare_program(rule: Rule) -> bool:
    return rule.open_end and len(rule.words) == 1


def exact_only(argv: list[str]) -> bool:
    """Commands only ever saved exactly: interpreters (their arguments are code), destructive
    commands, and a forced `git push`."""
    name = program_name(argv[0])
    if name in INTERPRETERS or name in DESTRUCTIVE:
        return True
    return name == "git" and argv[1:2] == ["push"] and any(a in ("--force", "-f") or a.startswith("--force") for a in argv[2:])


def _join(words: list[str]) -> str:
    return shlex.join(words)


def suggest_rule(argv: list[str]) -> str:
    """The rule "Always allow" offers for `argv` (spec §3.3). Never a bare `program *`."""
    if exact_only(argv):
        return _join(argv)
    name = program_name(argv[0])
    rest = argv[1:]
    if name in SUBCOMMAND_DEPTH:
        depth = SUBCOMMAND_DEPTH.get((name, rest[0]), SUBCOMMAND_DEPTH[name]) if rest else 0
        words: list[str] = []
        for word in rest[:depth]:
            if word.startswith("-"):
                break
            words.append(word)
        return _join([argv[0], *words]) + " *" if words else _join(argv)
    for i, word in enumerate(rest):
        if not word.startswith("-"):
            return _join([argv[0], *rest[: i + 1]]) + " *"
    return _join(argv)
```

In `src/phil/workspace/shell.py`, replace the body of `_program_name` so that it delegates. Then `_is_denied` compares by name on every platform, and the full-path bypass is closed.

```python
def _program_name(arg0: str) -> str:
    """The program `arg0` names, as compared with _RISKY_PROGRAMS (see permissions.rules)."""
    from phil.permissions.rules import program_name

    return program_name(arg0)
```

If that import is circular, move the import to the top of the module instead. `permissions.rules` imports only `phil.platform`, so there should be no cycle.

The existing test at `tests/workspace/test_shell.py:793-795` pins "POSIX unchanged". Update it to assert the new behaviour: a full path is compared by its name on POSIX too.

`suggest_rule` keeps `argv[0]` as typed, so `/usr/bin/gh pr checks 1` gives `/usr/bin/gh pr checks *`. `parse_rule` normalises it to `gh` when matching. The expected strings in the tests use bare names.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/permissions tests/workspace/test_shell.py -q -n 0`.

- [ ] **Step 5: Run the full suite, then commit**

Commit message: `Permissions: rules, program names and suggested rules; close the full-path bypass`.

---

### Task 2: The read-only catalog and labels (pure)

**Files:**
- Create: `src/phil/permissions/catalog.py`, `tests/permissions/test_catalog.py`

**Interfaces:**
- Consumes: `program_name`, `INTERPRETERS` and `DESTRUCTIVE` from Task 1.
- Produces:
  - `catalog_reads(argv: list[str]) -> bool`: True if `argv` is a read in the new catalog. This is not today's `READ_ONLY`, which stays in `shell.py`.
  - `label_for(argv: list[str], *, read_only: bool) -> str`: one of the three labels.
  - `KNOWN_PROGRAMS: frozenset[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/permissions/test_catalog.py
import pytest

from phil.permissions.catalog import catalog_reads, label_for

READS = [
    "gh pr view 42", "gh pr list", "gh pr checks 42 --watch", "gh pr diff 42", "gh pr status",
    "gh issue view 7", "gh issue list", "gh repo view", "gh run view 1", "gh run list",
    "gh release view v1", "gh release list", "gh auth status", "gh --version",
    "git remote", "git remote -v", "git rev-parse HEAD", "git blame src/x.py", "git tag -l", "git tag --list v*",
    "git stash list", "git config --get user.name", "git config --list",
    "npm ls", "npm view left-pad", "npm outdated", "npm why x", "pnpm ls", "yarn why x",
    "uv tree", "uv pip list", "uv pip show x", "uv python list", "uv --version",
    "cargo tree", "cargo metadata", "cargo version", "go list ./...", "go version", "go env GOPATH",
]
WRITES = [
    "gh pr merge 42", "gh pr create", "gh pr close 1", "gh pr edit 1", "gh api repos/x/y", "gh api -X GET x",
    "gh repo delete x", "gh release create v1",
    "git push", "git commit -m x", "git reset --hard", "git checkout main", "git tag v1", "git tag -d v1",
    "git remote add o url", "git config --add a.b c", "git config user.name x", "git stash",
    "npm install x", "npm run build", "npm publish", "uv add x", "uv sync", "uv run x", "uv pip install x",
    "cargo build", "cargo install x", "go get x", "go env -w GOPATH=/x",
]


@pytest.mark.parametrize("command", READS)
def test_reads(command):
    assert catalog_reads(command.split())


@pytest.mark.parametrize("command", WRITES)
def test_writes_are_not_reads(command):
    assert not catalog_reads(command.split())


def test_full_path_program_is_recognised():
    assert catalog_reads(["/opt/homebrew/bin/gh", "pr", "view", "1"])


@pytest.mark.parametrize("command,label", [
    ("gh pr view 1", "reads only"), ("gh pr merge 1", "changes things"), ("npm install x", "changes things"),
    ("rm build.log", "changes things"), ("python3 x.py", "changes things"),
    ("make test", "may change things"), ("terraform plan", "may change things"),
])
def test_labels(command, label):
    argv = command.split()
    assert label_for(argv, read_only=catalog_reads(argv)) == label


def test_label_for_existing_read_only_list():
    assert label_for(["ls", "-la"], read_only=True) == "reads only"
```

- [ ] **Step 2: Run them to verify they fail**

- [ ] **Step 3: Implement**

```python
# src/phil/permissions/catalog.py
"""Built-in read-only subcommands (spec 2026-10-13 §3.2) and the labels the approval callout shows.
Today's READ_ONLY list in workspace/shell.py stays as it is; this catalog adds subcommands of
programs that also have mutating ones."""

from phil.permissions.rules import DESTRUCTIVE, INTERPRETERS, program_name

# program -> {subcommand path (tuple of words): extra check on the remaining args, or None}
_GH = {
    ("pr", "view"): None, ("pr", "list"): None, ("pr", "checks"): None, ("pr", "diff"): None,
    ("pr", "status"): None, ("issue", "view"): None, ("issue", "list"): None, ("issue", "status"): None,
    ("repo", "view"): None, ("run", "view"): None, ("run", "list"): None, ("release", "view"): None,
    ("release", "list"): None, ("auth", "status"): None, ("workflow", "view"): None, ("workflow", "list"): None,
}
_GIT_CONFIG_READ = {"--get", "--get-all", "--get-regexp", "--list", "-l"}
_GIT_CONFIG_WRITE = {"--add", "--unset", "--unset-all", "--replace-all", "--edit", "-e",
                     "--rename-section", "--remove-section"}


def _git_remote(rest: list[str]) -> bool:
    return rest in ([], ["-v"], ["--verbose"])


def _git_tag(rest: list[str]) -> bool:
    return bool(rest) and rest[0] in ("-l", "--list")


def _git_config(rest: list[str]) -> bool:
    return bool(set(rest) & _GIT_CONFIG_READ) and not set(rest) & _GIT_CONFIG_WRITE


def _git_stash(rest: list[str]) -> bool:
    return rest[:1] == ["list"]


def _go_env(rest: list[str]) -> bool:
    return not any(a in ("-w", "-u") or a.startswith(("-w=", "-u=")) for a in rest)


_GIT = {("remote",): _git_remote, ("rev-parse",): None, ("blame",): None, ("tag",): _git_tag,
        ("stash",): _git_stash, ("config",): _git_config}
_NODE = {("ls",): None, ("list",): None, ("view",): None, ("info",): None, ("outdated",): None,
         ("why",): None, ("explain",): None}
_UV = {("tree",): None, ("pip", "list"): None, ("pip", "show"): None, ("pip", "freeze"): None,
       ("python", "list"): None}
_CARGO = {("tree",): None, ("metadata",): None, ("version",): None}
_GO = {("list",): None, ("version",): None, ("env",): _go_env}

CATALOG = {"gh": _GH, "git": _GIT, "npm": _NODE, "pnpm": _NODE, "yarn": _NODE, "uv": _UV,
           "cargo": _CARGO, "go": _GO}
KNOWN_PROGRAMS = frozenset({*CATALOG, "docker", "kubectl"})


def catalog_reads(argv: list[str]) -> bool:
    if not argv:
        return False
    name = program_name(argv[0])
    table = CATALOG.get(name)
    if table is None:
        return False
    rest = argv[1:]
    if rest in (["--version"], ["-v"], ["--help"], ["-h"]):
        return True
    for path, check in table.items():
        if tuple(rest[: len(path)]) == path:
            return check is None or check(rest[len(path):])
    return False


def label_for(argv: list[str], *, read_only: bool) -> str:
    if read_only:
        return "reads only"
    name = program_name(argv[0])
    if name in KNOWN_PROGRAMS or name in INTERPRETERS or name in DESTRUCTIVE:
        return "changes things"
    return "may change things"
```

**Ruling:** `gh api` with any method is never a read. Its absence from `_GH` already guarantees that.

- [ ] **Step 4: Run the tests to verify they pass**

- [ ] **Step 5: Run the full suite, then commit**

Commit message: `Permissions: read-only subcommand catalog and labels`.

---

### Task 3: Policy files on disk

**Files:**
- Create: `src/phil/permissions/store.py`, `tests/permissions/test_store.py`
- Modify: `src/phil/store/paths.py`. Add `ProjectPaths.policy_path` (`project_dir / "policy.toml"`) and a module function `global_policy_path() -> Path` (`phil_home() / "policy.toml"`).

**Interfaces:**
- Consumes: `parse_rule` and `RuleError` from Task 1; `lock_file` and `unlock_file` from `phil.platform`.
- Produces:
  - `PolicyEntry` (frozen dataclass: `rule: Rule`, `kind: Literal["allow", "deny"]`, `scope: str`, `source: str`). `scope` is `everywhere`, `<repo name>`, `<repo name> phil.toml` or `config`.
  - `PolicyProblem` (frozen dataclass: `path: Path`, `message: str`, `line: int | None`).
  - `PolicySet` (frozen dataclass: `deny: tuple[PolicyEntry, ...]`, `allow: tuple[PolicyEntry, ...]`, `problems: tuple[PolicyProblem, ...]`, `repo_rules_untrusted: tuple[str, ...]`).
  - `load_policy(*, repo_name: str, project_policy: Path, global_policy: Path, config_allow: list[str], repo_allow: list[str]) -> PolicySet`:
    - order: deny everywhere, then repo; allow everywhere, then repo, then trusted repo rules, then config;
    - fail safe on problems (spec §3.6).
  - `add_rule(path: Path, kind: str, rule_text: str, source: str, *, today: date) -> None`, which raises `PolicyWriteError`.
  - `remove_rule(path: Path, rule_text: str) -> bool`
  - `repo_rules_hash(rules: list[str]) -> str`, giving `"sha256:<hex>"` over `"\n".join(rules)`.
  - `record_repo_decision(path: Path, decision: Literal["trust", "ignore"], rules_hash: str) -> None`
  - `repo_decision(path: Path, rules_hash: str) -> str | None`, giving `"trust"`, `"ignore"` or None when there's no decision for this hash.

- [ ] **Step 1: Write the failing tests**

```python
# tests/permissions/test_store.py
import threading
from datetime import date
from pathlib import Path

import pytest

from phil.permissions.store import (
    PolicyWriteError, add_rule, load_policy, record_repo_decision, remove_rule, repo_decision, repo_rules_hash,
)

TODAY = date(2026, 10, 13)


def load(tmp_path: Path, *, config_allow=(), repo_allow=()):
    return load_policy(repo_name="calc", project_policy=tmp_path / "proj.toml", global_policy=tmp_path / "global.toml",
                       config_allow=list(config_allow), repo_allow=list(repo_allow))


def test_missing_files_are_empty(tmp_path):
    policy = load(tmp_path)
    assert policy.allow == () and policy.deny == () and policy.problems == ()


def test_add_rule_writes_and_loads_with_scopes(tmp_path):
    add_rule(tmp_path / "global.toml", "allow", "gh pr checks *", "approval in calc (r-1)", today=TODAY)
    add_rule(tmp_path / "proj.toml", "deny", "git push *", "manual", today=TODAY)
    policy = load(tmp_path, config_allow=["pytest *"])
    assert [(e.rule.text, e.scope) for e in policy.allow] == [("gh pr checks *", "everywhere"), ("pytest *", "config")]
    assert [(e.rule.text, e.scope) for e in policy.deny] == [("git push *", "calc")]


def test_comments_survive_a_write(tmp_path):
    path = tmp_path / "global.toml"
    path.write_text("# my notes\n\n[[allow]]\nrule = \"npm ls\"  # keep\n", encoding="utf-8", newline="\n")
    add_rule(path, "allow", "gh pr view *", "manual", today=TODAY)
    text = path.read_text(encoding="utf-8")
    assert "# my notes" in text and "# keep" in text and 'rule = "gh pr view *"' in text


def test_adding_the_same_rule_twice_keeps_one(tmp_path):
    path = tmp_path / "global.toml"
    add_rule(path, "allow", "gh pr view *", "manual", today=TODAY)
    add_rule(path, "allow", "gh pr view *", "manual", today=TODAY)
    assert path.read_text(encoding="utf-8").count("gh pr view *") == 1


def test_concurrent_writers_keep_every_rule(tmp_path):
    path = tmp_path / "global.toml"
    threads = [threading.Thread(target=add_rule, args=(path, "allow", f"make t{i} *", "manual"),
                                kwargs={"today": TODAY}) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(load(tmp_path).allow) == 8


def test_invalid_rule_is_refused_before_writing(tmp_path):
    with pytest.raises(PolicyWriteError):
        add_rule(tmp_path / "global.toml", "allow", "gh * view", "manual", today=TODAY)
    assert not (tmp_path / "global.toml").exists()


def test_broken_file_fails_safe(tmp_path):
    (tmp_path / "global.toml").write_text("[[allow]\nrule = 'x'\n", encoding="utf-8", newline="\n")
    add_rule(tmp_path / "proj.toml", "allow", "make test *", "manual", today=TODAY)
    add_rule(tmp_path / "proj.toml", "deny", "git push *", "manual", today=TODAY)
    policy = load(tmp_path, config_allow=["pytest *"])
    assert policy.problems and policy.problems[0].path == tmp_path / "global.toml"
    assert all(e.scope == "config" for e in policy.allow)  # saved allow rules switched off
    assert [e.rule.text for e in policy.deny] == ["git push *"]  # readable deny rules still apply
    with pytest.raises(PolicyWriteError):
        add_rule(tmp_path / "global.toml", "allow", "npm ls", "manual", today=TODAY)


def test_an_invalid_rule_inside_a_file_is_a_problem_with_its_line(tmp_path):
    (tmp_path / "proj.toml").write_text('[[allow]]\nrule = "gh * view"\n', encoding="utf-8", newline="\n")
    policy = load(tmp_path)
    assert policy.problems and policy.problems[0].line == 2


def test_remove_rule(tmp_path):
    path = tmp_path / "proj.toml"
    add_rule(path, "allow", "make test *", "manual", today=TODAY)
    assert remove_rule(path, "make test *") is True
    assert remove_rule(path, "make test *") is False
    assert load(tmp_path).allow == ()


def test_repo_rules_need_trust_for_their_hash(tmp_path):
    rules = ["make test *"]
    policy = load(tmp_path, repo_allow=rules)
    assert policy.repo_rules_untrusted == ("make test *",) and policy.allow == ()
    record_repo_decision(tmp_path / "proj.toml", "trust", repo_rules_hash(rules))
    policy = load(tmp_path, repo_allow=rules)
    assert [(e.rule.text, e.scope) for e in policy.allow] == [("make test *", "calc phil.toml")]
    assert repo_decision(tmp_path / "proj.toml", repo_rules_hash(rules)) == "trust"
    changed = ["make test *", "make deploy *"]
    assert repo_decision(tmp_path / "proj.toml", repo_rules_hash(changed)) is None
    assert load(tmp_path, repo_allow=changed).repo_rules_untrusted == tuple(changed)


def test_ignored_repo_rules_stay_off_without_nagging(tmp_path):
    rules = ["make test *"]
    record_repo_decision(tmp_path / "proj.toml", "ignore", repo_rules_hash(rules))
    policy = load(tmp_path, repo_allow=rules)
    assert policy.allow == () and policy.repo_rules_untrusted == ()


def test_config_allow_keeps_old_glob_patterns(tmp_path):
    policy = load(tmp_path, config_allow=["pytest tests/*.py"])
    assert policy.allow[0].scope == "config"
```

- [ ] **Step 2: Run them to verify they fail**

- [ ] **Step 3: Implement**

**Reading the files:**
- Read with `tomlkit.parse`.
- A file that is missing or empty is valid and empty.
- **A parse error** becomes a `PolicyProblem` with tomlkit's line.
- **A rule that `parse_rule` rejects** becomes a `PolicyProblem`. Its line is found by searching the file's lines for the rule string, with None if not found.
- **Any other wrong shape** also becomes a `PolicyProblem`: `allow` that isn't an array of tables, a missing `rule`, or an unknown `decision`.

**Fail safe.** If any problem is found, every entry from both policy files is dropped from `allow`, and so are the trusted repo rules. The config rules stay. Deny rules from the files that parsed cleanly stay.

**`[shell] allow` patterns from config** keep today's glob matching, so they can't go through `parse_rule`. Store them in a `PolicyEntry` whose `rule` is `Rule(text=pattern, words=(), open_end=False)`, marked with `scope="config"`. Task 4 matches config entries with the old matcher.

**Writing a rule** (`add_rule`):
1. Validate the rule with `parse_rule`, raising `PolicyWriteError` if it's invalid.
2. Open (or create) `<path>.lock` and hold `lock_file` for the whole read-modify-write.
3. Refuse with `PolicyWriteError` if the current file doesn't parse or has problems.
4. Skip the write if the same kind and text is already there.
5. Otherwise append an array-of-tables entry `{rule, added, from}`. The `added` value is a TOML date.
6. Write `<path>.tmp` with `encoding="utf-8", newline="\n"`, then `os.replace` it over the original.
7. Create the parent directory if it's missing.

**A new file** starts with a one-line comment:
- the everywhere file: `# Phil permissions — everywhere. \`phil permissions\` shows what's in effect.`
- a repo file: `# Phil permissions — <repo name>. …`

`add_rule` doesn't know the repo name. Pass the header text in as a keyword `header: str | None = None`, and have the callers (Tasks 5 and 7) give it.

**`remove_rule`** follows the same locking, removing matching entries of either kind.

**`record_repo_decision`** sets the `[repo_rules]` table's `decision` and `hash` under the same lock.

- [ ] **Step 4: Run the tests to verify they pass**

- [ ] **Step 5: Run the full suite, then commit**

Commit message: `Permissions: policy files (tomlkit, locked atomic writes, fail-safe loading, repo-rule trust)`.

---

### Task 4: `ShellPolicy` decides in the spec's order

**Files:**
- Modify:
  - `src/phil/workspace/shell.py`: `ShellPolicy`, plus a new `Decision`.
  - `src/phil/agents/tools.py`: `make_shell_tool`.
  - `src/phil/chat/approval.py:45-80` and `src/phil/run/gates.py:171-179`: the other constructors.
  - `src/phil/config.py`: `load_config` gets `include_repo: bool = True`.
  - Wherever `make_shell_tool` is called (`agents/invoke.py` and the engine): pass the policy set.
- Create: `src/phil/permissions/load.py`, with `policy_for(repo_root: Path, paths: ProjectPaths, config: PhilConfig) -> PolicySet`.
- Test: `tests/permissions/test_decide.py` (new), plus updates to existing shell, tools and approval tests.

**Interfaces:**
- Consumes:
  - Tasks 1–3;
  - `load_config(repo_root, include_repo=False)` for the config allow list, which is defaults plus `~/.phil/config.toml` only;
  - the repo's own list, read straight from `<repo_root>/phil.toml` `[shell] allow` with `tomllib`, giving `[]` if there is none.
- Produces:
  - `Decision` (frozen dataclass: `outcome: Literal["allow", "deny", "ask", "forbidden"]`, `reason: str`, `label: str`, `detail: str | None`).
  - `ShellPolicy(policy: PolicySet | None = None, *, extra_allow=(), approved=(), root=None, read_only: bool = False)`, with `decide(command) -> Decision`.
  - `denial_reason`, `refusal_detail` and `is_allowed` are kept as wrappers over `decide`:

| `decide` outcome | `denial_reason` returns |
|---|---|
| `forbidden` | `"forbidden"` |
| `deny` | `"denied"` |
| `ask` | `"not_allowed"` |
| `allow` | None |

**Ruling (compatibility).** `ShellPolicy([...])`, with a plain list as the first argument, must keep working for existing callers and tests. A list is turned into a `PolicySet` whose entries are config entries. Implement this by checking `isinstance(policy, list)`.

**The order, from spec §3.1:**
1. **Forbidden.** Today's checks, unchanged except for the program-name fix from Task 1, plus the existing `READ_ONLY` mutating flags and containment.
2. **Deny entries.** Rules are matched with `rule_matches`.
3. **Built-in reads.** Today's `READ_ONLY` key, or `catalog_reads(argv)`. Containment applies to catalog reads as well when `root` is set.
4. **Allow entries.**
   - Config entries use today's matcher (`_matches_pattern` over `_for_matching(shlex.split(pattern))`).
   - Saved and trusted entries use `rule_matches`.
   - Then `extra_allow`, as today, with the reason `the plan's test command`.
5. **Approved for this run.**
6. **Ask.**

**When `read_only=True`** (the read-only agents), steps 2, 4 and 5 are skipped, so only forbidden and built-in reads apply. Deny rules don't apply either, because those agents already can't do anything but read.

**Ruling.** A catalog read whose flags turn it into a write isn't forbidden. It simply isn't a read, so it falls through to the rules or to ask, and its label is `changes things`. Today's `READ_ONLY` entries keep making a mutating flag forbidden.

**The shell tool:**
- **On allow,** the output's first line stays `exit_code: …`. Add a second line, `allowed: <reason>`, so the reason reaches the agent and the activity details (`/more`, `phil show --step`).
- **On `deny`,** return `DENIED: \`<cmd>\` is blocked by your <reason>. Don't use it.` and do **not** add it to `log.denied`. A deny rule never becomes an approval prompt.
- **`ask`** behaves as today's `not_allowed`.

- [ ] **Step 1: Write the failing tests** (`tests/permissions/test_decide.py`). Use real `PolicySet`s built with `load_policy` from files written by `add_rule` in `tmp_path`.
  - **A deny rule beats a read:** `git log` with `[[deny]] git log *` gives outcome `deny` and reason `deny rule "git log *" (everywhere)`.
  - **A built-in read:** `gh pr view 1` gives `allow`, `read-only (built-in)` and `reads only`.
  - **A saved rule:** `gh pr merge 1` with repo `[[allow]] gh pr merge *` gives `allow` and `rule "gh pr merge *" (calc)`.
  - **A config glob:** `pytest tests/x.py` with `config_allow=["pytest *"]` gives `allow` and `rule "pytest *" (config)`.
  - **The plan's command:** extra_allow `make check` matches `make check -v`, giving `the plan's test command`.
  - **An approval:** approved `rm build.log` gives `approved for this run`, while `rm build.log -rf /` gives `ask`.
  - **Nothing matches:** `terraform apply` gives `ask`, `no rule matches` and `may change things`.
  - **Forbidden:** `/usr/bin/python3 -c x` gives `forbidden`, with a reason starting `forbidden: `.
  - **A broken everywhere file plus a repo allow rule:** the command asks, and `gh pr view 1` still gives `allow`.
  - **A read-only agent:** `read_only=True` with a repo allow rule `make test *`: `make test` gives `ask`, and `gh pr view 1` gives `allow`.
  - **A catalog read that writes:** `go env -w X=1` gives `ask` and `changes things`. It isn't forbidden.
  - **The shell tool:** the output contains `allowed: read-only (built-in)` for `git log`. A deny rule gives `DENIED:` and leaves `log.denied` empty.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement**, as described above.
  - `policy_for` gathers `config_allow`, `repo_allow`, `paths.policy_path` and `global_policy_path()`, and uses `repo_root.name` as `repo_name`.
  - **Wire it in:**
    - `make_shell_tool` gains `policy: PolicySet | None`. When it's None, it builds one from `shell.allow`, keeping old callers working.
    - The engine and `invoke` pass `policy_for(...)`, loaded once per agent call.
    - Read-only agents pass `read_only=True`.
- [ ] **Step 4: Run** `uv run pytest tests/permissions tests/workspace tests/agents tests/chat/test_approval.py tests/run -q -n 0`.
- [ ] **Step 5: Run the full suite, then commit.** Commit message: `Permissions: one ordered decision per command, with reasons and labels`.

---

### Task 5: The approval callout saves rules

**Files:**
- Modify:
  - `src/phil/run/engine.py`: the approval escalation (around line 655) and the resume handling of `approve` (around 855).
  - `src/phil/chat/decision.py`: `LABELS`, the `approval` branch around line 97, and `DEFAULT_FOR`.
  - `src/phil/cli/attach.py`.
  - The chat's resume path, wherever `approve` is sent today: `grep -n '"approve"' src/phil/chat`.
- Test: `tests/run/test_engine_approval.py`, `tests/chat/test_controller_callouts.py`, `tests/cli/test_attach*.py` and the decision tests (`grep -rln approval_decision tests`).

**Interfaces:**
- Consumes: `suggest_rule`, `exact_only` (Task 1); `catalog_reads`, `label_for` (Task 2); `add_rule`, `PolicyWriteError` (Task 3); `ProjectPaths.policy_path`, `global_policy_path`.
- Produces: an escalation whose `reason` is `"approval"` and which adds these fields:
  - `labels: list[str]`, parallel to `commands`;
  - `rules: list[str]`, the suggested rules, deduplicated in order;
  - `options`: `["approve", "always_repo", "always_everywhere", "deny", "abort"]`, with `always_everywhere` left out when `exact_only` holds for any command;
  - `repo: str`, the repo name.

**What happens on resume:**
- **`always_repo` or `always_everywhere`:**
  1. Write each rule with `add_rule(...)` to `paths.policy_path` or to `global_policy_path()`, with source `approval in <repo> (<run_id>)` and today's date.
  2. Then do exactly what `approve` does.
  3. Post one activity milestone with the verbatim feed line from the Global Constraints. Use whatever the engine already posts for milestones; read `run/engine.py` for the events or activity call used for "approved".
- **If `add_rule` raises `PolicyWriteError`,** still approve for the run. Post the milestone `Couldn't save rule: <error> · allowed for this run only`. The write failure never blocks the run.
- **Where the engine finds `ProjectPaths`:** check `RunDeps`. If it has no paths, add `paths: ProjectPaths` to `RunDeps` and pass it everywhere a `RunDeps` is built.

**The callout** (`decision.py`):
- The body lines are each command, padded so that its label sits right-aligned after it.
- The note line `Approving allows these exact commands for the rest of this run only.` is removed, because the options now say it.
- The options, using the verbatim labels from the Global Constraints:
  - `Allow for this run` (`approve`)
  - `Always allow in <repo>: <rules>` (`always_repo`)
  - `Always allow everywhere: <rules>` (`always_everywhere`), only when it's offered
  - `Deny` (`deny`)
  - `Stop the run` (`abort`)
- `LABELS["approve"]` becomes `Allow for this run`, `LABELS["deny"]` becomes `Deny`, and `LABELS["abort"]` becomes `Stop the run` **for approval decisions only**. Other pauses keep `Abort the run`. Build the approval options in the `approval` branch rather than changing the shared `LABELS`.
- The default stays `approve`.

**`phil attach`** offers the same answers. Its prompt lists each command with its label, then the rules being offered.

- [ ] **Step 1: Write the failing tests:**
  - **The engine escalation:** a denied `gh pr merge 42` and `make test-unit --fast` give `rules == ["gh pr merge *", "make test-unit *"]`, `labels == ["changes things", "may change things"]`, and options that include `always_everywhere`.
  - **An interpreter is denied:** `python3 x.py` gives options without `always_everywhere`, and the rule is `python3 x.py`.
  - **Resume with `always_repo`:** the repo policy file contains the rules, `approved` contains the commands, the next action is `implement` with `keep_worktree`, and the activity holds the feed line `Saved rules "gh pr merge *", "make test-unit *" for calc · /permissions to review`.
  - **Resume with `always_everywhere`:** writes to the global file under `PHIL_HOME`. Tests set `PHIL_HOME` to `tmp_path`.
  - **A broken policy file:** resuming with `always_repo` still approves, and posts `Couldn't save rule: …`.
  - **The decision callout:** the option labels are verbatim, and "everywhere" is missing when it isn't offered.
  - **`phil attach`:** offers the same answers.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/run tests/chat tests/cli -q -n 0`.
- [ ] **Step 5: Run the full suite, then commit.** Commit message: `Permissions: the approval callout can save rules for this repo or everywhere`.

---

### Task 6: Trusting a repo's `phil.toml` rules

**Files:**
- Modify:
  - `src/phil/chat/controller.py`: before plan approval is shown, and before a quick run starts.
  - `src/phil/chat/decision.py`: a new `repo_rules_decision(repo: str, rules: list[str], labels: list[str]) -> Decision`.
  - The worker start path (`src/phil/run/worker.py` or the engine's start), for the unattended notice.
- Test: `tests/chat/test_controller_repo_rules.py` (new), plus a worker test.

**Interfaces:**
- Consumes: `repo_rules_hash`, `repo_decision`, `record_repo_decision` (Task 3); `label_for`, `catalog_reads` (Task 2); `policy_for` (Task 4).
- Produces: `repo_rules_decision(...)`, whose options are `Trust these for <repo>` (answer `trust`) and `Ignore them` (answer `ignore`), with the title `<repo>'s phil.toml allows these commands`.

**When the chat asks.** Before the plan-approval callout, and before a quick run starts:
1. Read the repo's `[shell] allow`.
2. If it's non-empty and `repo_decision(...)` returns None, show `repo_rules_decision`.
3. Record the answer with `record_repo_decision`.
4. Only then continue to plan approval.

It asks at most once per hash, even across chats. A recorded decision ends the question.

**With no one to ask.** When a run starts and `PolicySet.repo_rules_untrusted` is non-empty, write one line to the run log (`events` or `phil.log`, whichever the worker already writes start-up notes to): `Ignoring <n> rule(s) from phil.toml that you haven't trusted: run \`phil permissions trust\` to use them`. This covers a worker started directly with `phil run`, and an "ignore" decision. An "ignore" decision is silent, because `repo_rules_untrusted` is empty for it.

- [ ] **Step 1: Write the failing tests:**
  - **The first plan with repo rules:** the trust callout appears before plan approval, and choosing trust records the decision.
  - **The next plan:** no callout.
  - **The list changed:** the callout appears again.
  - **The user chose ignore:** the rules stay off, and later plans show no callout.
  - **The quick path:** asks too.
  - **No repo rules:** no callout.
  - **The unattended worker:** with untrusted rules, the notice is written and the rules aren't applied. A `make test` covered only by repo rules is `ask`.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/chat tests/run -q -n 0`.
- [ ] **Step 5: Run the full suite, then commit.** Commit message: `Permissions: a repo's phil.toml rules need your trust`.

---

### Task 7: `phil permissions`, `/permissions`, and docs

**Files:**
- Create: `src/phil/cli/permissions.py`, a typer sub-app registered in `cli/main.py` as `permissions`, and `tests/cli/test_permissions_cli.py`.
- Modify:
  - `src/phil/chat/controller.py`: add `/permissions`, and add it to `HELP`.
  - `README.md`: a "Permissions" section.
  - `docs/superpowers/roadmap.md`: mark M4 done.

**Interfaces:**
- Consumes: everything above. `_open_project(ctx)` in `cli/main.py` gives `info` and `conn`; `ProjectPaths(info.slug)`.

**Commands,** from spec §3.7. Output is plain text through `console.print(..., markup=False)`, or escaped.

**`phil permissions`** prints these sections in order. A section with nothing in it is left out, except built-in:
- `Built-in: read-only commands (ls, grep, git log, gh pr view, npm ls, …) run without asking.`
- `Everywhere (~/.phil/policy.toml):`, with lines in the form `  allow  gh pr checks *   approval in calc (r-1) · 2026-10-13`.
- `calc (~/.phil/projects/<slug>/policy.toml):`
- `calc phil.toml (trusted)`, or `calc phil.toml (not trusted — phil permissions trust)` / `(ignored)`.
- `Config (~/.phil/config.toml [shell] allow):`

Any problems are printed first as `⚠ <path>:<line>: <message> — saved allow rules are off until it's fixed`.

**`phil permissions check "<command>"`** prints `<outcome word> · <label> · <reason>`, where the outcome word is `runs`, `asks`, `blocked` or `refused`. It exits 0 in every case.

**`phil permissions allow|deny "<rule>" [--everywhere] [--yes]`:**
- Validates with `parse_rule`.
- A bare `program *` without `--yes` prints a warning and exits 1.
- A rule whose words would be forbidden is refused, exiting 1. Check this with `ShellPolicy(None).decide(rule text without the trailing *)` returning `forbidden`.
- On success it prints `Added <kind> rule "<rule>" for <scope>.`

**`phil permissions remove "<rule>" [--everywhere]`:**
- On success: `Removed "<rule>".`
- Otherwise it lists rules in that file that share the program, as `No rule "<rule>". Similar: …`, and exits 1.

**`phil permissions trust|ignore`** records the decision for the current hash and prints `Trusted 2 rules from phil.toml.` / `Ignoring 2 rules from phil.toml.`. With no repo rules it prints `phil.toml has no [shell] allow rules.`

**`/permissions`** in the chat prints the same text as `phil permissions`, then `Change rules with \`phil permissions allow|deny|remove\`.`

- [ ] **Step 1: Write the failing tests,** using the existing CLI test harness (`grep -rln "CliRunner" tests/cli | head -3`) and `PHIL_HOME=tmp_path`. Cover:
  - each command;
  - `check` for a forbidden command, a deny rule, a built-in read and an ask;
  - a bare `gh *` warning and exiting 1 without `--yes`;
  - `remove` with near matches;
  - `trust` and `ignore`;
  - listing with a broken file, which prints the warning first;
  - `/permissions` in the chat.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.** In the README section, explain:
  - the order of checks;
  - the two policy files;
  - the trust of a repo's `phil.toml` rules;
  - the commands.

  In `docs/superpowers/roadmap.md`, mark the M4 section done, with links to the spec and plan, and tick it off in the "Release sequence".
- [ ] **Step 4: Run** `uv run pytest tests/cli tests/chat tests/permissions -q -n 0`.
- [ ] **Step 5: Run the full suite, then commit.** Commit message: `Permissions: phil permissions, /permissions, README and roadmap`.
