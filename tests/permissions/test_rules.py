import shlex

import pytest

from phil import platform
from phil.permissions.rules import (
    Rule, RuleError, exact_only, is_bare_program, parse_rule, program_name, rule_matches, suggest_rule,
)
from phil.workspace.shell import ShellPolicy, _for_matching


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
    assert rule_matches(rule, ["gh", "pr", "checks", "42"])
    assert not rule_matches(rule, ["gh", "pr", "checks", "42", "--watch"])  # R9
    assert not rule_matches(rule, ["./gh", "pr", "checks"])
    assert not rule_matches(rule, ["/usr/local/bin/gh", "pr", "checks"])
    assert not rule_matches(rule, ["gh", "pr", "merge", "42"])
    exact = parse_rule("make test")
    assert rule_matches(exact, ["make", "test"]) and not rule_matches(exact, ["make", "test", "-j4"])


def test_a_path_rule_matches_only_that_exact_path():
    assert rule_matches(parse_rule("/usr/local/bin/gh pr *"), ["/usr/local/bin/gh", "pr", "x"])


@pytest.mark.parametrize("text", ["", "*", "gh * view", "gh pr*", "gh 'unclosed"])
def test_invalid_rules(text):
    with pytest.raises(RuleError):
        parse_rule(text)


@pytest.mark.parametrize("text", ["make test\nls", "make test\r", "make\ttest", "make\x00 test", "gh pr view\x1b[0m *"])
def test_rules_with_control_characters_are_rejected(text):
    with pytest.raises(RuleError):
        parse_rule(text)


def test_bare_program():
    assert is_bare_program(parse_rule("gh *")) and not is_bare_program(parse_rule("gh pr *"))


@pytest.mark.parametrize("argv,rule", [
    (["gh", "pr", "checks", "42"], "gh pr checks *"),
    (["git", "push", "origin", "main"], "git push *"),
    (["npm", "install", "left-pad"], "npm install *"),
    (["uv", "pip", "install", "x"], "uv pip install *"),
    (["docker", "compose", "up", "-d"], "docker compose up -d"),  # R9: an option after the subcommand
    (["make", "test-unit", "--fast"], "make test-unit --fast"),
    (["make", "test-unit"], "make test-unit *"),
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
        suggestion = suggest_rule(argv)
        assert suggestion is not None
        assert not is_bare_program(parse_rule(suggestion))


@pytest.mark.parametrize("argv", [["rm", "*"], ["make", "*"], ["gh", "pr", "*"], ["ls", "a*b"], []])
def test_unsavable_commands_have_no_suggestion(argv):
    assert suggest_rule(argv) is None


def test_exact_only_is_safe_on_empty_argv():
    assert exact_only([]) is False


@pytest.mark.parametrize("argv", [
    ["git", "push", "--force", "origin"], ["git", "push", "--force-with-lease"], ["git", "push", "-fu", "origin"],
    ["git", "push", "-uf", "origin"], ["git", "-C", "x", "push", "-f"], ["git", "push", "--mirror"],
    ["git", "push", "--delete", "origin", "b"], ["git", "push", "--prune", "origin"], ["git", "push", "-d", "origin", "b"],
    ["git", "push", "origin", "+main"], ["git", "push", "origin", ":old"],
])
def test_exact_only_for_forced_and_destructive_pushes(argv):
    assert exact_only(argv) is True


@pytest.mark.parametrize("argv", [["git", "push", "origin", "main"], ["git", "push", "-u", "origin", "main"],
                                  ["git", "-C", "x", "push", "origin"], ["git", "commit", "-f"]])
def test_exact_only_leaves_ordinary_pushes_alone(argv):
    assert exact_only(argv) is False


def test_arguments_with_spaces_round_trip():
    argv = ["make", "my target", "x"]
    rule = parse_rule(suggest_rule(argv))
    assert rule_matches(rule, argv)
    exact = parse_rule(suggest_rule(["ruff", "--config", "a b.toml"]))
    assert rule_matches(exact, ["ruff", "--config", "a b.toml"])


@pytest.mark.parametrize("argv0", ["./gh", "../gh", "sub/gh", "/tmp/gh"])
def test_a_path_never_matches_a_bare_name_rule(argv0):
    assert not rule_matches(parse_rule("gh *"), [argv0, "pr"])


def test_windows_path_rules(monkeypatch):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    rule = parse_rule("C:/x/gh.exe pr *")
    assert rule_matches(rule, [r"C:\x\gh.exe", "pr", "1"])
    assert rule_matches(rule, ["c:/X/GH.EXE", "pr", "1"])
    assert not rule_matches(rule, ["gh", "pr", "1"])
    assert not rule_matches(rule, [r"C:\y\gh.exe", "pr", "1"])
    bare = parse_rule("gh pr *")
    assert rule_matches(bare, ["GH.EXE", "pr", "1"])
    assert not rule_matches(bare, [r"C:\x\gh.exe", "pr", "1"])
    assert not rule_matches(bare, [r"..\gh", "pr", "1"])
    assert not rule_matches(bare, [r".\gh.exe", "pr", "1"])


@pytest.mark.parametrize("text", ["rm '*'", r"gh pr \*", 'gh pr "*"', 'rm "a*"', '"" *', "gh 'pr' '*'"])
def test_a_quoted_or_escaped_star_is_not_a_wildcard(text):
    with pytest.raises(RuleError):
        parse_rule(text)


@pytest.mark.parametrize("char", ["\t", "\x1b", " ", " ", "‮", "​", "", "͸", "\ud800"])
def test_a_rule_with_a_hidden_character_is_refused(char):
    # N1: control (Cc), format (Cf), line and paragraph separators (Zl, Zp), private-use (Co) and
    # unassigned (Cn) characters and lone surrogates (Cs) can hide or reorder what a rule says, so
    # no rule holds one.
    with pytest.raises(RuleError):
        parse_rule(f"make 'a{char}b' *")


def test_a_bare_trailing_star_is_still_open_ended():
    rule = parse_rule("gh pr *")
    assert rule.open_end and rule.words == ("gh", "pr")
    assert parse_rule("gh 'pr view' *").words == ("gh", "pr view")


def test_open_ended_rules_never_cover_dangerous_forms():
    push = parse_rule("git push *")
    assert rule_matches(push, ["git", "push", "origin", "main"])
    assert not rule_matches(push, ["git", "push", "-f", "origin"])
    assert not rule_matches(push, ["git", "-C", "x", "push", "--force"])
    assert not rule_matches(parse_rule("rm *"), ["rm", "x"])
    assert not rule_matches(Rule(text="rm *", words=("rm",), open_end=True), ["rm", "x"])
    assert rule_matches(parse_rule("rm x"), ["rm", "x"])


@pytest.mark.parametrize("argv", [
    ["env", "bash", "-c", "x"], ["sudo", "rm", "x"], ["xargs", "rm"], ["uv", "run", "python", "-c", "x"],
    ["git", "config", "alias.x", "!rm"], ["python3.12", "x.py"], ["deno", "run", "x"], ["RM", "-rf", "x"],
    ["Python3", "x.py"], ["uv", "tool", "run", "x"], ["uvx", "x"], ["npm", "exec", "x"], ["pnpm", "dlx", "x"],
    ["cargo", "run"], ["go", "run", "x.go"], ["docker", "run", "img"], ["kubectl", "exec", "p"],
    ["pkill", "x"], ["pwsh", "-c", "x"], ["python3.12.1", "x"], ["git", "-c", "a=b", "config", "x"],
])
def test_dangerous_suggestions_are_exact(argv):
    assert exact_only(argv) is True
    assert suggest_rule(argv) == shlex.join(argv)


@pytest.mark.parametrize("argv", [["pythonista", "x"], ["uv", "pip", "list"], ["cargo", "test"], ["go", "test"],
                                  ["docker", "compose", "up"], ["npm", "install", "x"]])
def test_ordinary_programs_are_not_exact_only(argv):
    assert exact_only(argv) is False


def test_risky_programs_are_forbidden_whatever_the_case():
    assert ShellPolicy([]).denial_reason("PYTHON3 -c x") == "forbidden"


def test_windows_path_comparison_keeps_the_extension(monkeypatch):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    rule = parse_rule("./gh.exe pr *")
    assert rule_matches(rule, [".\\GH.EXE", "pr", "x"])
    assert not rule_matches(rule, ["./gh.bat", "pr", "x"])
    assert not rule_matches(rule, ["./gh", "pr", "x"])
    assert not rule_matches(rule, ["./gh.cmd", "pr", "x"])
    assert rule_matches(parse_rule("./tools/gh.exe pr *"), ["./tools/gh.exe", "pr", "1"])


@pytest.mark.parametrize("text", [r"gh pr view a\ b", r"gh echo C:\x\y", "gh 'a b' *", r"./tools\gh.exe pr *"])
def test_a_rule_is_tokenised_as_the_shell_policy_does(monkeypatch, text):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    expected = _for_matching(shlex.split(text))
    words = parse_rule(text).words
    fixed = expected[:-1] if expected[-1] == "*" else expected
    assert words[1:] == tuple(fixed[1:])
    assert words[0] == (fixed[0] if "/" in fixed[0] else program_name(fixed[0]))


@pytest.mark.parametrize("argv,expected", [
    (["python3", "x.py"], True), (["/usr/bin/node", "x.js"], True), (["bash", "x.sh"], True),
    (["rm", "-rf", "build"], True), (["kill", "12"], True), (["git", "push", "-f"], True),
    (["git", "push", "origin"], False), (["gh", "pr", "merge", "1"], False), (["make", "test"], False),
])
def test_exact_only(argv, expected):
    assert exact_only(argv) is expected


# --- fix round 3 -------------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["tar", "xzf", "a.tgz"], ["rsync", "-e", "x", "a", "b"], ["sed", "-n", "1p", "f"], ["ssh", "host", "ls"],
    ["cmd", "/c", "dir"], ["terraform", "plan"], ["bash5", "x"], ["docker", "compose", "-f", "x", "up"],
    ["uv", "pip", "--system", "install", "x"], ["git", "-C", "d", "status"],
])
def test_only_vetted_families_get_open_suggestions(argv):
    assert suggest_rule(argv) == shlex.join(argv)


@pytest.mark.parametrize("argv,rule", [
    (["docker", "container", "ls"], "docker container ls *"),
    (["pytest", "-q", "tests/"], "pytest -q tests/ *"),
    (["gh", "pr", "list"], "gh pr list *"),
    (["kubectl", "get", "pods"], "kubectl get *"),
    (["/usr/bin/gh", "pr", "view", "1"], "/usr/bin/gh pr view *"),
])
def test_vetted_families_get_open_suggestions(argv, rule):
    assert suggest_rule(argv) == rule


def _covered(rule: str, argv: list[str]) -> bool:
    return rule_matches(parse_rule(rule), argv)


@pytest.mark.parametrize("argv", [
    ["git", "push", "--del", "origin", "main"], ["git", "push", "--mirr", "origin"], ["git", "push", "--pru", "origin"],
    ["git", "push", "--receive-pack=x", "."], ["git", "push", "--exec=x", "."], ["git", "push", "--force-if-includes"],
    ["git", "push", "--force-with-lease=x"],
])
def test_an_open_push_rule_never_covers_a_dangerous_push_option(argv):  # R9 (was an exact_only option list)
    assert not _covered("git push *", argv)
    assert suggest_rule(argv) == shlex.join(argv)


@pytest.mark.parametrize("argv", [["git", "push", "--tags"], ["git", "push", "-u", "origin", "feat"]])
def test_harmless_push_options_are_not_exact_only(argv):
    assert exact_only(argv) is False


@pytest.mark.parametrize("rule,argv", [
    ("git fetch *", ["git", "fetch", "--upload-pack=x", "."]), ("git fetch *", ["git", "fetch", "--upload", "x", "."]),
    ("git pull *", ["git", "pull", "--upload-pack=x"]), ("git ls-remote *", ["git", "ls-remote", "--upload-pack=x", "."]),
    ("git clone *", ["git", "clone", "-u", "x", ".", "d"]), ("git clone *", ["git", "clone", "--template=./t", "u"]),
    ("git clone *", ["git", "clone", "--config=core.fsmonitor=x", "u"]),
    ("git rebase *", ["git", "rebase", "--exec", "x", "main"]), ("git rebase *", ["git", "rebase", "-x", "x", "main"]),
    ("git grep *", ["git", "grep", "--open-files-in-pager=x", "foo"]), ("git grep *", ["git", "grep", "-Ox", "foo"]),
    ("git log *", ["git", "log", "--extcmd=x"]), ("git log *", ["git", "-c", "a=b", "log"]),
])
def test_an_open_git_rule_never_covers_an_option_that_runs_a_command(rule, argv):  # R9 (was an exact_only list)
    assert not _covered(rule, argv)
    assert suggest_rule(argv) == shlex.join(argv)


@pytest.mark.parametrize("argv", [
    ["git", "submodule", "foreach", "x"], ["git", "bisect", "run", "x"], ["git", "difftool", "HEAD"],
    ["git", "mergetool"], ["git", "filter-branch"], ["git", "config", "a", "b"],
])
def test_git_subcommands_that_run_a_command_are_exact_only(argv):
    assert exact_only(argv) is True


@pytest.mark.parametrize("argv", [
    ["git", "clone", "url"], ["git", "fetch", "origin"], ["git", "log", "--oneline"], ["git", "status"],
    ["git", "grep", "foo"], ["git", "submodule", "update", "--init"], ["git", "bisect", "start"],
])
def test_ordinary_git_is_not_exact_only(argv):
    assert exact_only(argv) is False


@pytest.mark.parametrize("argv", [
    ["docker", "run", "i"], ["docker", "exec", "c", "x"], ["docker", "create", "i"], ["docker", "container", "run", "i"],
    ["docker", "container", "exec", "c"], ["docker", "container", "create", "i"], ["docker", "compose", "run", "w"],
    ["docker", "compose", "exec", "w"], ["kubectl", "exec", "p"], ["kubectl", "run", "p"], ["kubectl", "debug", "p"],
    ["kubectl", "attach", "p"], ["kubectl", "-n", "ns", "exec", "p"], ["docker", "-H", "h", "run", "i"],
    ["go", "-C", "d", "run", "x.go"], ["cargo", "+nightly", "run"], ["uv", "--directory", "d", "run", "x"],
    ["gh", "api", "repos/o/r"], ["gh", "api", "graphql", "-f", "q=x"],
])
def test_launcher_subcommands_are_found_at_any_position(argv):
    assert exact_only(argv) is True


@pytest.mark.parametrize("argv", [["docker", "ps"], ["docker", "compose", "up"], ["kubectl", "get", "pods"],
                                  ["gh", "pr", "view", "1"], ["npm", "install", "x"]])
def test_ordinary_container_and_gh_commands_are_not_exact_only(argv):
    assert exact_only(argv) is False


@pytest.mark.parametrize("argv", [
    ["bash5", "x"], ["perl5.30", "x.pl"], ["node20", "x.js"], ["cmd", "/c", "dir"], ["wsl", "ls"],
    ["cscript", "x.vbs"], ["wscript", "x.vbs"], ["mshta", "x.hta"], ["ruby3.2", "x"],
])
def test_versioned_interpreters_and_windows_launchers_are_exact_only(argv):
    assert exact_only(argv) is True


def test_unicode_folds_of_dangerous_names_are_exact_only():
    assert exact_only(["ſudo", "rm", "x"]) is True
    assert exact_only(["ſh", "-c", "x"]) is True
    assert suggest_rule(["ſudo", "rm", "x"]) is None
    assert suggest_rule(["ﬁnd", ".", "-name", "x"]) is None
    assert ShellPolicy([]).denial_reason("ｐｙｔｈｏｎ３ -c x") == "forbidden"


def test_a_non_ascii_program_never_matches_a_bare_name_rule():
    assert not rule_matches(parse_rule("sudo rm x"), ["ſudo", "rm", "x"])
    assert not rule_matches(parse_rule("find ."), ["ﬁnd", "."])
    assert rule_matches(parse_rule("find ."), ["find", "."])


@pytest.mark.parametrize("argv", [["", "x"], ["gh", "pr", ""], ["gh", ""]])
def test_an_empty_word_cannot_be_saved(argv):
    assert suggest_rule(argv) is None


# --- fix round 4 -------------------------------------------------------------------------------

@pytest.mark.parametrize("rule,argv", [
    ("git rebase *", ["git", "rebase", "-xsh", "main"]), ("git rebase *", ["git", "rebase", "-ix", "sh", "main"]),
    ("git clone *", ["git", "clone", "-ux", ".", "d"]), ("git clone *", ["git", "clone", "-qu", "x", ".", "d"]),
    ("git clone *", ["git", "clone", "-qc", "a=b", "u"]), ("git grep *", ["git", "grep", "-iOx", "foo"]),
    ("git ls-remote *", ["git", "ls-remote", "-qu", "x", "."]),
])
def test_an_open_git_rule_never_covers_a_short_option_cluster(rule, argv):  # P1, now R9
    assert not _covered(rule, argv)
    assert suggest_rule(argv) == shlex.join(argv)


@pytest.mark.parametrize("argv", [
    ["git", "send-email", "--to-cmd=x", "p"], ["git", "instaweb", "--httpd=x"],
    ["git", "daemon", "--access-hook=x", "."],
])
def test_git_subcommands_that_run_a_command_are_exact_only_p2(argv):  # P2
    assert exact_only(argv) is True


@pytest.mark.parametrize("argv", [["git", "apply", "--unsafe-paths", "p"], ["git", "apply", "--unsafe", "p"]])
def test_an_open_apply_rule_never_covers_unsafe_paths(argv):  # P2, now R9
    assert not _covered("git apply *", argv)
    assert suggest_rule(argv) == shlex.join(argv)


@pytest.mark.parametrize("argv,rule", [
    (["docker", "volume", "ls"], "docker volume ls *"),
    (["docker", "plugin", "ls"], "docker plugin ls *"),
    (["docker", "system", "df"], "docker system df *"),
    (["docker", "image", "ls"], "docker image ls *"),
    (["docker", "context", "ls"], "docker context ls *"),
    (["docker", "stack", "ls"], "docker stack ls *"),
    (["docker", "network", "ls"], "docker network ls *"),
    (["uv", "tool", "list"], "uv tool list *"),
])
def test_two_level_groups_keep_both_words(argv, rule):  # P3, P8
    assert suggest_rule(argv) == rule


def test_a_two_level_rule_does_not_cover_a_sibling():  # P3, P8
    assert not rule_matches(parse_rule(suggest_rule(["docker", "plugin", "ls"])),
                            ["docker", "plugin", "install", "--grant-all-permissions", "x"])
    assert not rule_matches(parse_rule(suggest_rule(["uv", "tool", "list"])), ["uv", "tool", "install", "x"])


@pytest.mark.parametrize("argv", [
    ["docker", "build", "-t", "a", "."], ["docker", "buildx", "bake"], ["docker", "image", "build", "."],
    ["docker", "buildx", "build", "-t", "a", "."], ["docker", "builder", "build", "."],
])
def test_docker_builds_are_exact_only(argv):  # P4
    assert exact_only(argv) is True


@pytest.mark.parametrize("argv", [
    ["kubectl", "cp", "a", "b"], ["kubectl", "config", "view", "--raw"], ["kubectl", "config", "use-context", "p"],
])
def test_kubectl_cp_and_config_are_exact_only(argv):  # P5
    assert exact_only(argv) is True


@pytest.mark.parametrize("argv", [
    ["npm", "config", "set", "a=b"], ["npm", "set", "a=b"], ["pnpm", "config", "set", "a", "b"],
    ["yarn", "config", "set", "-H", "a", "b"], ["gh", "config", "set", "pager", "x"],
])
def test_config_setters_that_can_name_a_command_are_exact_only(argv):  # P6
    assert exact_only(argv) is True


def test_npm_set_is_only_the_subcommand_in_first_position():  # P6
    assert exact_only(["npm", "install", "set"]) is False


@pytest.mark.parametrize("rule,argv", [
    ("go build *", ["go", "build", "-toolexec=x", "."]), ("go build *", ["go", "build", "--toolexec", "x", "."]),
    ("go vet *", ["go", "vet", "-vettool=x", "."]), ("go env *", ["go", "env", "-w", "GOFLAGS=-toolexec=x"]),
    ("go build *", ["go", "build", "GOFLAGS=-toolexec=x"]),
])
def test_an_open_go_rule_never_covers_an_option_that_runs_a_command(rule, argv):  # P7, now R9
    assert not _covered(rule, argv)
    assert suggest_rule(argv) == shlex.join(argv)


def test_go_tool_is_exact_only():  # P7
    assert exact_only(["go", "tool", "x"]) is True


@pytest.mark.parametrize("argv", [
    ["gh", "codespace", "ssh", "-c", "x"], ["gh", "cs", "ssh", "-c", "x"], ["npm", "explore", "p", "--", "ls"],
])
def test_gh_codespace_ssh_and_npm_explore_are_exact_only(argv):  # P9, P10
    assert exact_only(argv) is True


@pytest.mark.parametrize("argv,real", [
    (["npm", "publish", "--dry-run"], ["npm", "publish"]), (["cargo", "publish", "--dry-run"], ["cargo", "publish"]),
    (["uv", "publish", "--dry-run"], ["uv", "publish"]),
    (["kubectl", "delete", "pod", "x", "--dry-run=client"], ["kubectl", "delete", "pod", "x"]),
    (["kubectl", "apply", "--dry-run=server", "-f", "x.yaml"], ["kubectl", "apply", "-f", "x.yaml"]),
    (["git", "push", "--dry-run", "origin", "feat"], ["git", "push", "origin", "feat"]),
    (["git", "push", "-n", "origin", "feat"], ["git", "push", "origin", "feat"]),
    (["git", "push", "-un", "origin", "feat"], ["git", "push", "-u", "origin", "feat"]),
    (["git", "clean", "-n"], ["git", "clean", "-fdx"]), (["git", "push", "--dry-run"], ["git", "push"]),
])
def test_a_dry_run_is_saved_exactly(argv, real):  # P11, R8, now R9: a dry run is an option
    assert suggest_rule(argv) == shlex.join(argv)
    assert not _covered(suggest_rule(argv), real)


def test_an_open_rule_never_covers_a_dry_run():  # R8
    assert not rule_matches(parse_rule("npm publish *"), ["npm", "publish", "--dry-run"])
    assert rule_matches(parse_rule("npm publish --dry-run"), ["npm", "publish", "--dry-run"])


@pytest.mark.parametrize("argv", [
    ["gh", "repo", "delete", "o/r"], ["gh", "release", "delete", "v1"], ["gh", "secret", "remove", "K"],
    ["kubectl", "delete", "ns", "prod"], ["docker", "volume", "rm", "v"], ["docker", "system", "prune", "-af"],
    ["docker", "image", "prune"], ["docker", "rm", "c"], ["docker", "kill", "c"], ["npm", "uninstall", "x"],
    ["npm", "unpublish", "x"], ["pnpm", "remove", "x"], ["yarn", "remove", "x"], ["uv", "remove", "x"],
    ["cargo", "uninstall", "x"], ["cargo", "clean"], ["go", "clean", "-cache"], ["git", "reset", "--hard"],
    ["git", "clean", "-fd"], ["git", "stash", "drop"], ["git", "rm", "x"], ["git", "worktree", "remove", "w"],
    ["git", "remote", "remove", "o"], ["make", "clean"], ["just", "destroy"], ["gh", "cache", "del", "k"],
    ["kubectl", "-n", "ns", "delete", "pod", "x"], ["gh", "run", "delete", "1"], ["docker", "network", "prune"],
    ["kubectl", "delete", "pod", "x"],
])
def test_remove_verbs_are_saved_exactly(argv):  # R8
    assert exact_only(argv) is True
    assert suggest_rule(argv) == shlex.join(argv)


def test_an_open_rule_never_covers_a_remove_verb():  # R8
    assert not rule_matches(parse_rule("gh repo *"), ["gh", "repo", "delete", "o/r"])
    assert not rule_matches(parse_rule("docker volume *"), ["docker", "volume", "rm", "v"])
    assert rule_matches(parse_rule("gh repo *"), ["gh", "repo", "view", "o/r"])


@pytest.mark.parametrize("argv", [
    ["git", "commit", "-m", "msg"], ["git", "push", "origin", "feat"], ["git", "rebase", "-i", "main"],
    ["git", "status", "-sb"], ["git", "push", "-u", "origin", "feat"], ["git", "clone", "-q", "url"],
    ["go", "build", "./..."], ["go", "vet", "./..."], ["docker", "compose", "up", "-d"],
    ["kubectl", "get", "pods"], ["npm", "install", "x"], ["gh", "config", "get", "pager"],
    ["docker", "volume", "ls"], ["uv", "tool", "list"],
])
def test_ordinary_family_commands_stay_open(argv):
    assert exact_only(argv) is False


@pytest.mark.parametrize("argv,rule", [
    (["git", "push", "origin", "feat"], "git push *"),
    (["docker", "compose", "up", "web"], "docker compose up *"),
    (["go", "build", "./..."], "go build *"),
])
def test_ordinary_suggestions_are_unchanged(argv, rule):
    assert suggest_rule(argv) == rule


@pytest.mark.parametrize("argv", [["ſudo", "rm", "x"], ["ﬁnd", "."], ["ｇｈ", "pr", "view"], ["./ſh", "x"]])
def test_a_non_ascii_program_cannot_be_saved(argv):  # N1
    assert suggest_rule(argv) is None


# --- fix round 5 (R9: a rule's `*` covers plain arguments only) ---------------------------------

def test_a_star_covers_plain_arguments_only():  # R9
    rule = parse_rule("gh pr checks *")
    assert rule_matches(rule, ["gh", "pr", "checks", "43"])
    assert rule_matches(rule, ["gh", "pr", "checks", "43", "44"])
    for argv in (["gh", "pr", "checks", "43", "--watch"], ["gh", "pr", "checks", "--", "x"],
                 ["gh", "pr", "checks", "a=b"], ["gh", "pr", "checks", "-"], ["gh", "pr", "checks", "-w"]):
        assert not rule_matches(rule, argv)


def test_options_in_the_fixed_words_still_match():  # R9 applies only after the fixed words
    assert rule_matches(parse_rule("make -j4 test *"), ["make", "-j4", "test", "unit"])
    assert rule_matches(parse_rule("make test --fast"), ["make", "test", "--fast"])


@pytest.mark.parametrize("rule,argv", [
    ("git grep *", ["git", "grep", "-3Ox", "y"]),  # B1
    ("git grep *", ["git", "grep", "-i3Osh ./evil.sh", "foo"]),  # B1
    ("gh repo clone *", ["gh", "repo", "clone", "o/r", "--", "-c", "core.hooksPath=x"]),  # B2
    ("gh repo fork *", ["gh", "repo", "fork", "o/r", "--clone", "--", "-c", "core.hooksPath=x"]),  # B2
    ("gh gist clone *", ["gh", "gist", "clone", "abc", "--", "--template=/w/tpl"]),  # B2
    ("kubectl get *", ["kubectl", "get", "pods", "--kubeconfig", "./k.yaml"]),  # B3
    ("kubectl get *", ["kubectl", "get", "pods", "--kubeconfig=./k.yaml"]),  # B3
    ("kubectl get *", ["kubectl", "get", "pods", "-s", "https://h"]),  # B3
    ("go env *", ["go", "env", "-w", "CC=./evil.sh"]),  # B4
])
def test_re_review_4_escapes_are_not_covered(rule, argv):  # B1 to B4
    assert not _covered(rule, argv)


@pytest.mark.parametrize("seen,rule", [
    (["git", "grep", "foo"], "git grep *"), (["gh", "repo", "clone", "o/r"], "gh repo clone *"),
    (["kubectl", "get", "pods"], "kubectl get *"), (["go", "env", "GOPATH"], "go env *"),
    (["gh", "pr", "checks", "42"], "gh pr checks *"),
])
def test_plain_argument_suggestions_stay_open(seen, rule):  # R9
    assert suggest_rule(seen) == rule
    assert _covered(rule, seen)


@pytest.mark.parametrize("argv", [
    ["npm", "install", "-D", "vitest"], ["gh", "pr", "checks", "42", "--watch"], ["npm", "install", "a=b"],
    ["git", "push", "origin", "feat", "--", "x"], ["pytest", "tests/", "-k", "x"],
    ["docker", "volume", "ls", "--format", "x"],
])
def test_an_option_or_assignment_after_the_subcommand_gives_the_exact_command(argv):  # R9
    assert suggest_rule(argv) == shlex.join(argv)


@pytest.mark.parametrize("argv", [
    ["docker", "plugin"], ["docker", "context"], ["uv", "tool"], ["gh", "repo"], ["gh", "pr"],
])
def test_a_command_stopping_at_a_group_word_is_saved_exactly(argv):  # Q2
    assert suggest_rule(argv) == shlex.join(argv)


def test_a_forced_or_destructive_push_is_still_exact_only():  # keeps "everywhere" out (Task 5)
    for argv in (["git", "push", "-f"], ["git", "push", "--force-with-lease"], ["git", "push", "-4f", "o"],
                 ["git", "push", "origin", "+main"], ["git", "push", "origin", ":old"]):
        assert exact_only(argv) is True
    assert not _covered("git push *", ["git", "push", "origin", "+main"])  # a refspec is a plain argument
