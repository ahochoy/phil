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
    assert rule_matches(rule, ["gh", "pr", "checks", "42", "--watch"])
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
