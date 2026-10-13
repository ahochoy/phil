"""Built-in read-only subcommands (spec 2026-10-13 §3.2, ruling R10) and the labels the approval
callout shows. Today's READ_ONLY list in workspace/shell.py stays as it is; this catalog adds
subcommands of programs that also have mutating ones.

A command is a read only when every word after its subcommand path is a plain argument (R9: no
leading `-`, no `=`) or an option whose name, the part before any `=`, is on that entry's own
`safe_flags`. Any other option, and `--`, is not a read."""

from collections.abc import Callable
from dataclasses import dataclass

from phil.permissions.rules import exact_only, has_separator, is_plain_argument, program_name


@dataclass(frozen=True)
class Entry:
    """One read-only subcommand: the options it tolerates, and an extra check on the words after
    the subcommand path for the few entries that need one (a read mode that must be named)."""

    safe_flags: frozenset[str] = frozenset()
    check: Callable[[list[str]], bool] | None = None


def _flags(*names: str) -> frozenset[str]:
    return frozenset(names)


_GH_READ = _flags(
    "--json", "--jq", "-q", "--template", "-t", "--limit", "-L", "--state", "-s", "--web", "-w",
    "--repo", "-R", "--comments", "-c", "--author", "-A", "--label", "-l", "--base", "-B", "--head",
    "-H", "--search", "-S", "--branch", "-b", "--assignee", "-a", "--patch", "--name-only", "--color",
    "--log", "--log-failed", "--watch", "--required", "--interval", "-i", "--exit-status", "--attempt",
    "--job", "-j",
)
_GH = {
    path: Entry(_GH_READ)
    for path in (
        ("pr", "view"), ("pr", "list"), ("pr", "checks"), ("pr", "diff"), ("pr", "status"),
        ("issue", "view"), ("issue", "list"), ("issue", "status"), ("repo", "view"), ("run", "view"),
        ("run", "list"), ("release", "view"), ("release", "list"), ("workflow", "view"),
        ("workflow", "list"),
    )
}
# `gh auth status --show-token` (`-t`) prints a secret: `-t` is not on this entry's list, and
# neither is any abbreviation of `--show-token`.
_GH[("auth", "status")] = Entry(_flags("--hostname"))


def _git_remote(rest: list[str]) -> bool:
    return not any(is_plain_argument(word) for word in rest)  # `remote add o url` is a write


def _git_tag(rest: list[str]) -> bool:
    return any(word in ("-l", "--list") for word in rest)  # listing must be asked for: `tag v1` creates


def _git_config(rest: list[str]) -> bool:
    return any(word in ("--get", "--get-all", "--get-regexp", "--list", "-l") for word in rest)


def _nothing_after(rest: list[str]) -> bool:
    return not rest


_GIT = {
    ("remote",): Entry(_flags("-v", "--verbose"), _git_remote),
    ("rev-parse",): Entry(_flags(
        "--abbrev-ref", "--short", "--show-toplevel", "--show-prefix", "--git-dir",
        "--is-inside-work-tree", "--verify", "-q", "--quiet", "--symbolic-full-name",
    )),
    ("blame",): Entry(_flags(
        "-L", "-w", "-M", "-C", "-e", "-s", "-l", "--porcelain", "--line-porcelain", "--show-email", "-p",
    )),
    ("tag",): Entry(_flags(
        "-l", "--list", "-n", "--sort", "--contains", "--no-contains", "--points-at", "--merged",
        "--no-merged", "--format",
    ), _git_tag),
    ("config",): Entry(_flags(
        "--get", "--get-all", "--get-regexp", "--list", "-l", "--local", "--global", "--system",
        "--show-origin", "--show-scope", "--name-only", "--null", "-z", "--default", "--type",
    ), _git_config),
    ("stash", "list"): Entry(check=_nothing_after),  # `--output=<file>` writes: nothing may follow
}
_NPM_READ = _flags(
    "--json", "--all", "-a", "--depth", "--long", "-l", "--parseable", "-p", "--omit", "--include",
    "--workspace", "-w", "--workspaces",
)
_NPM = {(name,): Entry(_NPM_READ) for name in ("ls", "list", "outdated", "why", "explain", "view", "info")}
# pnpm and yarn run a package.json script for a name they don't know, so each table lists only the
# built-ins that exist (yarn's differ by version, so only `why`).
_PNPM_READ = _flags(
    "--json", "--depth", "--long", "--parseable", "--prod", "-P", "--dev", "-D", "--recursive", "-r",
    "--filter", "--global", "-g",
)
_PNPM = {(name,): Entry(_PNPM_READ) for name in ("ls", "list", "why", "outdated")}
_YARN = {("why",): Entry()}
_UV_PIP = _flags(
    "--format", "--editable", "-e", "--exclude-editable", "--strict", "--files", "-f", "--outdated",
)  # not --python/-p: uv runs that file to query it
_UV = {
    ("pip", "list"): Entry(_UV_PIP), ("pip", "show"): Entry(_UV_PIP), ("pip", "freeze"): Entry(_UV_PIP),
    ("python", "list"): Entry(),
}
_CARGO = {("version",): Entry()}
_GO = {
    ("list",): Entry(_flags("-json", "-m", "-e", "-f", "-u", "-versions", "-deps", "-find")),
    ("version",): Entry(_flags("-m", "-v")),
    ("env",): Entry(_flags("-json", "-changed")),  # not -w/-u (they edit the go env file)
}

CATALOG = {"gh": _GH, "git": _GIT, "npm": _NPM, "pnpm": _PNPM, "yarn": _YARN, "uv": _UV,
           "cargo": _CARGO, "go": _GO}
KNOWN_PROGRAMS = frozenset({*CATALOG, "docker", "kubectl"})


def _words_allowed(entry: Entry, rest: list[str]) -> bool:
    for word in rest:
        if word.startswith("-"):
            if word.split("=", 1)[0] not in entry.safe_flags:  # also rejects `-` and `--`
                return False
        elif not is_plain_argument(word):
            return False
    return entry.check is None or entry.check(rest)


def catalog_reads(argv: list[str]) -> bool:
    """Whether `argv` is a built-in read. Only a bare ASCII program name counts (R3): a path such as
    `./gh` or `/tmp/gh` may be something the agent wrote, so it is never taken for the real one."""
    if not argv or has_separator(argv[0]) or not argv[0].isascii():
        return False
    table = CATALOG.get(program_name(argv[0]))
    if table is None:
        return False
    rest = argv[1:]
    if rest in (["--version"], ["--help"], ["-h"]):
        return True
    for path, entry in table.items():
        if tuple(rest[: len(path)]) == path:
            return _words_allowed(entry, rest[len(path):])
    return False


def label_for(argv: list[str], *, read_only: bool) -> str:
    """The approval callout's label: a read, a command known to change things, or one that might."""
    if read_only:
        return "reads only"
    if argv and (exact_only(argv) or program_name(argv[0]) in KNOWN_PROGRAMS):
        return "changes things"
    return "may change things"
