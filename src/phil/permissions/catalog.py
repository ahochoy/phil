"""Built-in read-only subcommands (spec 2026-10-13 §3.2, rulings R10 to R12) and the labels the
approval callout shows. Today's READ_ONLY list in workspace/shell.py stays as it is; this catalog
adds subcommands of programs that also have mutating ones.

A command is a read only when every word after its subcommand path is a plain argument (R9: no
leading `-`, no `=`) or an option whose name, the part before any `=`, is on that entry's own
`safe_flags`. Any other option, and `--`, is not a read. A safe flag in `valued` takes a value
(R12): the next word is consumed as it (and must not look like an option), or the flag is
written `--opt=value`. An entry's `check` sees only the parsed options and plain arguments, never a
consumed value.

uv, pnpm and yarn have no entries (R11): they run code named in repo-local config files the agent
can write (`pyproject.toml`, `.yarnrc.yml`, `.pnpmfile.cjs`)."""

from collections.abc import Callable
from dataclasses import dataclass

from phil.permissions.rules import exact_only, has_separator, is_plain_argument, program_name


@dataclass(frozen=True)
class Entry:
    """One read-only subcommand: the options it tolerates, which of those take a value, whether a
    valued short flag may carry its value attached (`-L5`), and an extra check on the parsed
    words for the few entries that need one (a read mode that must be named)."""

    safe_flags: frozenset[str] = frozenset()
    valued: frozenset[str] = frozenset()
    check: Callable[[list[str]], bool] | None = None
    attached: bool = False

    def __post_init__(self) -> None:
        assert self.valued <= self.safe_flags


def _flags(*names: str) -> frozenset[str]:
    return frozenset(names)


# gh: `-R`/`--repo` can point at any host, so the request goes where the agent says (a query, not a
# write). `--jq` and `--template` run locally and can print environment variables; Phil strips
# secret-named ones from `child_env`. `--web` and `-w` are left out: they open a browser at a URL
# gh builds from the agent's arguments.
_GH_READ = _flags(
    "--json", "--jq", "-q", "--template", "-t", "--limit", "-L", "--state", "-s",
    "--repo", "-R", "--comments", "-c", "--author", "-A", "--label", "-l", "--base", "-B", "--head",
    "-H", "--search", "-S", "--branch", "-b", "--assignee", "-a", "--patch", "--name-only", "--color",
    "--log", "--log-failed", "--watch", "--required", "--interval", "-i", "--exit-status", "--attempt",
    "--job", "-j",
)
_GH_VALUED = _flags(
    "--json", "--jq", "-q", "--template", "-t", "--limit", "-L", "--state", "-s", "--repo", "-R",
    "--author", "-A", "--label", "-l", "--base", "-B", "--head", "-H", "--search", "-S", "--branch",
    "-b", "--assignee", "-a", "--interval", "-i", "--attempt", "--job", "-j", "--color",
)
_GH = {
    path: Entry(_GH_READ, _GH_VALUED, attached=True)
    for path in (
        ("pr", "view"), ("pr", "list"), ("pr", "checks"), ("pr", "diff"), ("pr", "status"),
        ("issue", "view"), ("issue", "list"), ("issue", "status"), ("repo", "view"), ("run", "view"),
        ("run", "list"), ("release", "view"), ("release", "list"), ("workflow", "view"),
        ("workflow", "list"),
    )
}
# `gh auth status --show-token` (`-t`) prints a secret: `-t` is not on this entry's list, and
# neither is any abbreviation of `--show-token`.
_GH[("auth", "status")] = Entry(_flags("--hostname"), _flags("--hostname"))


def _git_remote(parsed: list[str]) -> bool:
    return not any(is_plain_argument(word) for word in parsed)  # `remote add o url` is a write


def _git_tag(parsed: list[str]) -> bool:
    return any(word in ("-l", "--list") for word in parsed)  # listing must be asked for: `tag v1` creates


def _git_config(parsed: list[str]) -> bool:
    return any(word in ("--get", "--get-all", "--get-regexp", "--list", "-l") for word in parsed)


def _nothing_after(parsed: list[str]) -> bool:
    return not parsed


_GIT = {
    ("remote",): Entry(_flags("-v", "--verbose"), check=_git_remote),
    ("rev-parse",): Entry(_flags(
        "--abbrev-ref", "--short", "--show-toplevel", "--show-prefix", "--git-dir",
        "--is-inside-work-tree", "--verify", "-q", "--quiet", "--symbolic-full-name",
    )),
    # `-M` and `-C` take only an attached number, so they are not valued.
    ("blame",): Entry(_flags(
        "-L", "-w", "-M", "-C", "-e", "-s", "-l", "--porcelain", "--line-porcelain", "--show-email", "-p",
    ), _flags("-L"), attached=True),
    ("tag",): Entry(_flags(
        "-l", "--list", "-n", "--sort", "--contains", "--no-contains", "--points-at", "--merged",
        "--no-merged", "--format",
    ), _flags("--sort", "--contains", "--no-contains", "--points-at", "--merged", "--no-merged", "--format"),
        _git_tag),
    ("config",): Entry(_flags(
        "--get", "--get-all", "--get-regexp", "--list", "-l", "--local", "--global", "--system",
        "--show-origin", "--show-scope", "--name-only", "--null", "-z", "--default", "--type",
    ), _flags("--default", "--type"), _git_config),
    ("stash", "list"): Entry(check=_nothing_after),  # `--output=<file>` writes: nothing may follow
}
_NPM_READ = _flags(
    "--json", "--all", "-a", "--depth", "--long", "-l", "--parseable", "-p", "--omit", "--include",
    "--workspace", "-w", "--workspaces",
)
_NPM_VALUED = _flags("--depth", "--omit", "--include", "--workspace", "-w")
_NPM = {
    (name,): Entry(_NPM_READ, _NPM_VALUED)
    for name in ("ls", "list", "outdated", "why", "explain", "view", "info")
}
_CARGO = {("version",): Entry()}
_GO = {
    ("list",): Entry(_flags("-json", "-m", "-e", "-f", "-u", "-versions", "-deps", "-find"), _flags("-f")),
    ("version",): Entry(_flags("-m", "-v")),
    ("env",): Entry(_flags("-json", "-changed")),  # not -w/-u (they edit the go env file)
}

CATALOG = {"gh": _GH, "git": _GIT, "npm": _NPM, "cargo": _CARGO, "go": _GO}
KNOWN_PROGRAMS = frozenset({*CATALOG, "docker", "kubectl", "uv", "pnpm", "yarn"})


def _parse(entry: Entry, rest: list[str]) -> list[str] | None:
    """The options (by name) and plain arguments in `rest`, without any consumed values; None when
    a word isn't allowed or a valued flag has no usable value."""
    parsed: list[str] = []
    i = 0
    while i < len(rest):
        word = rest[i]
        i += 1
        if not word.startswith("-"):
            if not is_plain_argument(word):
                return None
            parsed.append(word)
            continue
        name = word.split("=", 1)[0]
        if name in entry.safe_flags:  # also rejects `-` and `--`
            if "=" not in word and name in entry.valued:
                if i >= len(rest) or rest[i].startswith("-"):
                    return None
                i += 1
            parsed.append(name)
        elif (entry.attached and "=" not in word and len(word) > 2 and word[1] != "-"
              and word[:2] in entry.valued):
            parsed.append(word[:2])
        else:
            return None
    return parsed


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
            parsed = _parse(entry, rest[len(path):])
            return parsed is not None and (entry.check is None or entry.check(parsed))
    return False


def label_for(argv: list[str], *, read_only: bool) -> str:
    """The approval callout's label: a read, a command known to change things, or one that might."""
    if read_only:
        return "reads only"
    if argv and (exact_only(argv) or program_name(argv[0]) in KNOWN_PROGRAMS):
        return "changes things"
    return "may change things"
