"""Built-in read-only subcommands (spec 2026-10-13 §3.2) and the labels the approval callout shows.
Today's READ_ONLY list in workspace/shell.py stays as it is; this catalog adds subcommands of
programs that also have mutating ones."""

from collections.abc import Callable

from phil.permissions.rules import exact_only, has_separator, program_name

_Check = Callable[[list[str]], bool] | None


def _gh_auth_status(rest: list[str]) -> bool:
    """`gh auth status` prints a secret under `--show-token` / `-t`, alone or in a short cluster."""
    for arg in rest:
        if arg.startswith("--"):
            if "--show-token".startswith(arg.split("=", 1)[0]) and len(arg) > 2:
                return False
        elif arg.startswith("-") and "t" in arg:
            return False
    return True


# program -> {subcommand path (tuple of words): extra check on the remaining args, or None}
_GH: dict[tuple[str, ...], _Check] = {
    ("pr", "view"): None, ("pr", "list"): None, ("pr", "checks"): None, ("pr", "diff"): None,
    ("pr", "status"): None, ("issue", "view"): None, ("issue", "list"): None, ("issue", "status"): None,
    ("repo", "view"): None, ("run", "view"): None, ("run", "list"): None, ("release", "view"): None,
    ("release", "list"): None, ("auth", "status"): _gh_auth_status, ("workflow", "view"): None,
    ("workflow", "list"): None,
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


_GIT: dict[tuple[str, ...], _Check] = {
    ("remote",): _git_remote, ("rev-parse",): None, ("blame",): None, ("tag",): _git_tag,
    ("stash",): _git_stash, ("config",): _git_config,
}
_NODE: dict[tuple[str, ...], _Check] = {
    ("ls",): None, ("list",): None, ("view",): None, ("info",): None, ("outdated",): None,
    ("why",): None, ("explain",): None,
}
_UV: dict[tuple[str, ...], _Check] = {
    ("tree",): None, ("pip", "list"): None, ("pip", "show"): None, ("pip", "freeze"): None,
    ("python", "list"): None,
}
_CARGO: dict[tuple[str, ...], _Check] = {("tree",): None, ("metadata",): None, ("version",): None}
_GO: dict[tuple[str, ...], _Check] = {("list",): None, ("version",): None, ("env",): _go_env}

CATALOG = {"gh": _GH, "git": _GIT, "npm": _NODE, "pnpm": _NODE, "yarn": _NODE, "uv": _UV,
           "cargo": _CARGO, "go": _GO}
KNOWN_PROGRAMS = frozenset({*CATALOG, "docker", "kubectl"})


def catalog_reads(argv: list[str]) -> bool:
    """Whether `argv` is a built-in read. Only a bare ASCII program name counts (R3): a path such as
    `./gh` or `/tmp/gh` may be something the agent wrote, so it is never taken for the real one."""
    if not argv or has_separator(argv[0]) or not argv[0].isascii():
        return False
    table = CATALOG.get(program_name(argv[0]))
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
    """The approval callout's label: a read, a command known to change things, or one that might."""
    if read_only:
        return "reads only"
    if argv and (exact_only(argv) or program_name(argv[0]) in KNOWN_PROGRAMS):
        return "changes things"
    return "may change things"
