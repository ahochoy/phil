"""`phil permissions` (spec 2026-10-13 §3.7): list the rules in effect, check a command, and add,
remove, trust or ignore rules."""

import tomllib

import pytest
from typer.testing import CliRunner

from phil.cli import main as cli
from phil.cli import permissions as permissions_cli
from phil.permissions.listing import shown_path
from phil.permissions.load import repo_shell_settings
from phil.permissions.store import repo_decision, repo_shell_hash
from phil.repo import resolve_repo
from phil.store.paths import ProjectPaths, global_policy_path
from phil.ui.theme import make_console
from tests.helpers import MODELS_TOML

runner = CliRunner()


def wide(monkeypatch):
    # Wide enough that no line wraps, so the assertions can match whole lines.
    monkeypatch.setattr(permissions_cli, "console", make_console(width=300))


def phil(repo, *args):
    return runner.invoke(cli.app, ["--repo", str(repo), "permissions", *args])


def project_policy(repo):
    return ProjectPaths(resolve_repo(repo).slug).policy_path


def with_shell(repo, shell: str):
    (repo / "phil.toml").write_text(MODELS_TOML + "\n[shell]\n" + shell, encoding="utf-8", newline="\n")


def rules_in(path, kind="allow"):
    with open(path, "rb") as handle:
        return [entry["rule"] for entry in tomllib.load(handle).get(kind, [])]


# --- allow and deny ------------------------------------------------------------------------------


def test_allow_adds_a_rule_for_this_repo(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "allow", "gh pr checks *")
    assert result.exit_code == 0, result.output
    assert 'Added allow rule "gh pr checks *" for calc.' in result.output
    assert 'Note: "*" covers plain arguments only (no options).' in result.output
    path = project_policy(calc_repo)
    assert rules_in(path) == ["gh pr checks *"]
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Phil permissions — calc. `phil permissions` shows what's in effect.")
    with open(path, "rb") as handle:
        assert tomllib.load(handle)["allow"][0]["from"] == "manual"


def test_allow_everywhere_writes_the_global_file(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "allow", "make   test", "--everywhere")
    assert result.exit_code == 0, result.output
    assert 'Added allow rule "make test" for everywhere.' in result.output
    assert "Note:" not in result.output  # an exact rule has no `*` to explain
    assert rules_in(global_policy_path()) == ["make test"]
    assert global_policy_path().read_text(encoding="utf-8").startswith("# Phil permissions — everywhere.")
    assert not project_policy(calc_repo).exists()


def test_deny_adds_a_deny_rule(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "deny", "git push *")
    assert result.exit_code == 0, result.output
    assert 'Added deny rule "git push *" for calc.' in result.output
    assert "Note:" not in result.output  # a deny rule's `*` covers options too (R13)
    assert rules_in(project_policy(calc_repo), "deny") == ["git push *"]


def test_an_invalid_rule_is_refused(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "allow", "gh * pr")
    assert result.exit_code == 1
    assert "`*` is only allowed, bare, as the last word" in result.output
    assert not project_policy(calc_repo).exists()


def test_a_bare_program_rule_needs_yes(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "allow", "gh *")
    assert result.exit_code == 1
    assert '"gh *" allows gh with any plain arguments' in result.output
    assert "--yes" in result.output
    assert not project_policy(calc_repo).exists()
    result = phil(calc_repo, "allow", "gh *", "--yes")
    assert result.exit_code == 0, result.output
    assert 'Added allow rule "gh *" for calc.' in result.output
    assert 'Note: "*" covers plain arguments only (no options).' in result.output
    assert rules_in(project_policy(calc_repo)) == ["gh *"]


def test_an_open_rule_for_an_unvetted_program_needs_yes(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "allow", "terraform apply *")
    assert result.exit_code == 1
    assert "Phil hasn't vetted terraform. Add --yes to save it anyway." in result.output
    assert not project_policy(calc_repo).exists()
    assert phil(calc_repo, "allow", "terraform apply *", "--yes").exit_code == 0
    assert phil(calc_repo, "allow", "terraform plan").exit_code == 0  # an exact rule needs no --yes
    assert rules_in(project_policy(calc_repo)) == ["terraform apply *", "terraform plan"]


def test_a_forbidden_rule_is_refused_with_the_reason(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "allow", "python3 -c *")
    assert result.exit_code == 1
    assert '"python3 -c *" is forbidden' in result.output
    assert "forbidden:" in result.output
    assert not project_policy(calc_repo).exists()


def test_writing_to_a_broken_file_is_refused(calc_repo, monkeypatch):
    wide(monkeypatch)
    path = project_policy(calc_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[[allow]\n", encoding="utf-8", newline="\n")
    for args in (("allow", "make test"), ("deny", "make deploy"), ("remove", "make test")):
        result = phil(calc_repo, *args)
        assert result.exit_code == 1, args
        assert f"{shown_path(path)} can't be read (" in result.output
        assert "). Fix it by hand, then try again." in result.output
    assert path.read_text(encoding="utf-8") == "[[allow]\n"


# --- remove --------------------------------------------------------------------------------------


def test_remove_deletes_the_rule(calc_repo, monkeypatch):
    wide(monkeypatch)
    phil(calc_repo, "allow", "gh pr checks *")
    phil(calc_repo, "deny", "gh pr merge *")
    result = phil(calc_repo, "remove", "gh  pr checks *")
    assert result.exit_code == 0, result.output
    assert 'Removed "gh pr checks *".' in result.output
    assert rules_in(project_policy(calc_repo)) == []
    assert rules_in(project_policy(calc_repo), "deny") == ["gh pr merge *"]


def test_remove_lists_near_matches_when_nothing_matches(calc_repo, monkeypatch):
    wide(monkeypatch)
    phil(calc_repo, "allow", "gh pr checks *")
    phil(calc_repo, "deny", "gh pr merge *")
    phil(calc_repo, "allow", "make test")
    result = phil(calc_repo, "remove", "gh pr view *")
    assert result.exit_code == 1
    assert 'No rule "gh pr view *". Similar: "gh pr checks *", "gh pr merge *"' in result.output
    assert "make test" not in result.output


def test_remove_says_when_it_removed_both_an_allow_and_a_deny(calc_repo, monkeypatch):
    wide(monkeypatch)
    phil(calc_repo, "allow", "make deploy")
    phil(calc_repo, "deny", "make deploy")
    result = phil(calc_repo, "remove", "make deploy")
    assert result.exit_code == 0, result.output
    assert result.output == 'Removed allow and deny "make deploy".\n'


def test_remove_points_a_config_pattern_at_config_toml(calc_repo, monkeypatch):
    from phil.config import global_config_path

    wide(monkeypatch)
    result = phil(calc_repo, "remove", "pytest *")  # a built-in default [shell] allow pattern
    assert result.exit_code == 1
    assert result.output == (
        f'"pytest *" comes from {shown_path(global_config_path())} [shell] allow — edit it there.\n')


@pytest.mark.parametrize("rule", ["sudo *", "python *", "git push -f *"])
def test_allow_refuses_a_rule_that_can_never_match_without_yes(calc_repo, monkeypatch, rule):
    wide(monkeypatch)
    result = phil(calc_repo, "allow", rule)
    assert result.exit_code == 1
    assert result.output == (
        f'"{rule}" can never match — Phil saves commands like this exactly. Add --yes to save it anyway.\n')
    assert not project_policy(calc_repo).exists()
    result = phil(calc_repo, "allow", rule, "--yes")
    assert result.exit_code == 0, result.output
    assert f'Note: "{rule}" can never match — Phil saves commands like this exactly. Saved anyway.' in result.output
    assert rules_in(project_policy(calc_repo)) == [rule]


def test_remove_everywhere_uses_the_global_file(calc_repo, monkeypatch):
    wide(monkeypatch)
    phil(calc_repo, "allow", "make test", "--everywhere")
    result = phil(calc_repo, "remove", "make test")
    assert result.exit_code == 1
    assert 'No rule "make test".' in result.output
    result = phil(calc_repo, "remove", "make test", "--everywhere")
    assert result.exit_code == 0, result.output
    assert rules_in(global_policy_path()) == []


# --- check ---------------------------------------------------------------------------------------


def test_check_a_built_in_read(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "check", "git log")
    assert result.exit_code == 0, result.output
    assert result.output == "runs · reads only · read-only (built-in)\n"


def test_check_an_ask(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "check", "terraform apply")
    assert result.exit_code == 0, result.output
    assert result.output == "asks · may change things · no rule matches\n"


def test_check_an_allow_rule(calc_repo, monkeypatch):
    wide(monkeypatch)
    phil(calc_repo, "allow", "gh pr checks *")
    result = phil(calc_repo, "check", "gh pr checks 42")
    assert result.output == 'runs · reads only · read-only (built-in)\n'  # a built-in read comes first
    result = phil(calc_repo, "check", "gh pr checks 42 --watch")
    assert result.output == 'runs · reads only · read-only (built-in)\n'
    phil(calc_repo, "allow", "make test *")
    result = phil(calc_repo, "check", "make test unit")
    assert result.output == 'runs · may change things · rule "make test *" (calc)\n'


def test_check_a_deny_rule(calc_repo, monkeypatch):
    wide(monkeypatch)
    phil(calc_repo, "deny", "git push *", "--everywhere")
    result = phil(calc_repo, "check", "git push -f origin main")
    assert result.exit_code == 0, result.output
    assert result.output == 'blocked · changes things · deny rule "git push *" (everywhere)\n'


def test_check_a_forbidden_command(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "check", "ls; rm -rf /")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("refused · ")
    assert " · forbidden: " in result.output


# --- trust and ignore ----------------------------------------------------------------------------


def recorded(repo):
    return repo_decision(project_policy(repo), repo_shell_hash(repo_shell_settings(repo)))


TRUST_LIST = (
    "calc's phil.toml allows these commands\n"
    "  make test *  may change things\n"
    "  make lint    may change things\n"
    "  passes these environment variables to agent commands: REPO_TOKEN\n"
)


def test_trust_with_yes_shows_what_it_trusts_and_records_it(calc_repo, monkeypatch):
    wide(monkeypatch)
    with_shell(calc_repo, 'allow = ["make test *", "make lint"]\npass_env = ["REPO_TOKEN"]\n')
    result = phil(calc_repo, "trust", "--yes")
    assert result.exit_code == 0, result.output
    assert result.output == TRUST_LIST + "Trusted 2 rules and pass_env from phil.toml.\n"
    assert recorded(calc_repo) == "trust"
    result = phil(calc_repo, "check", "make test unit")
    assert result.output == 'runs · may change things · rule "make test *" (calc phil.toml)\n'


def test_trust_without_a_terminal_or_yes_shows_the_list_and_records_nothing(calc_repo, monkeypatch):
    wide(monkeypatch)
    with_shell(calc_repo, 'allow = ["make test *", "make lint"]\npass_env = ["REPO_TOKEN"]\n')
    result = phil(calc_repo, "trust")
    assert result.exit_code == 1
    assert result.output == TRUST_LIST + "Run with --yes to trust these without a prompt.\n"
    assert recorded(calc_repo) is None


def on_a_terminal(monkeypatch, answer, then=None):
    """`phil permissions trust` as if run in a terminal, answering its prompt with `answer`."""
    prompts = []

    def prompt(text, **kwargs):
        prompts.append(text)
        if then is not None:
            then()
        return answer

    monkeypatch.setattr(permissions_cli, "_on_a_terminal", lambda: True)
    monkeypatch.setattr(permissions_cli.typer, "prompt", prompt)
    return prompts


def test_trust_on_a_terminal_asks_first(calc_repo, monkeypatch):
    wide(monkeypatch)
    with_shell(calc_repo, 'allow = ["make test *", "make lint"]\npass_env = ["REPO_TOKEN"]\n')
    prompts = on_a_terminal(monkeypatch, "y")
    result = phil(calc_repo, "trust")
    assert result.exit_code == 0, result.output
    assert prompts == ["Trust these for calc? [y/N]"]
    assert result.output == TRUST_LIST + "Trusted 2 rules and pass_env from phil.toml.\n"
    assert recorded(calc_repo) == "trust"


@pytest.mark.parametrize("answer", ["", "n", "no"])
def test_trust_on_a_terminal_records_nothing_unless_you_say_yes(calc_repo, monkeypatch, answer):
    wide(monkeypatch)
    with_shell(calc_repo, 'allow = ["make test *"]\n')
    on_a_terminal(monkeypatch, answer)
    result = phil(calc_repo, "trust")
    assert result.exit_code == 0, result.output
    assert result.output.endswith("Nothing recorded.\n")
    assert recorded(calc_repo) is None


def test_trust_records_the_hash_of_what_it_printed(calc_repo, monkeypatch):
    wide(monkeypatch)
    with_shell(calc_repo, 'allow = ["make test *"]\n')
    shown = repo_shell_hash(repo_shell_settings(calc_repo))
    on_a_terminal(monkeypatch, "y", then=lambda: with_shell(calc_repo, 'allow = ["make deploy *"]\n'))
    result = phil(calc_repo, "trust")
    assert result.exit_code == 0, result.output
    assert "make deploy" not in result.output
    assert repo_decision(project_policy(calc_repo), shown) == "trust"
    assert recorded(calc_repo) is None  # the changed settings were never shown, so they're not trusted


def test_trust_shows_hidden_characters_as_question_marks(calc_repo, monkeypatch):
    wide(monkeypatch)
    with_shell(calc_repo, 'allow = ["make \\u001b[31mtest *"]\nbash = "C:/b\\tash.exe"\n')
    result = phil(calc_repo, "trust", "--yes")
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output and "\t" not in result.output
    assert "  make ?[31mtest *" in result.output
    assert "  runs every command through: C:/b?ash.exe\n" in result.output


def test_ignore_shows_the_list_and_records_without_asking(calc_repo, monkeypatch):
    wide(monkeypatch)
    with_shell(calc_repo, 'allow = ["make test *"]\npass_env = ["A"]\nbash = "/bin/bash"\n')
    result = phil(calc_repo, "ignore")
    assert result.exit_code == 0, result.output
    assert result.output == (
        "calc's phil.toml allows these commands\n"
        "  make test *  may change things\n"
        "  passes these environment variables to agent commands: A\n"
        "  runs every command through: /bin/bash\n"
        "Ignoring 1 rule, pass_env and bash from phil.toml.\n"
    )
    assert recorded(calc_repo) == "ignore"
    assert phil(calc_repo, "check", "make test unit").output.startswith("asks · ")


def test_trust_with_nothing_set(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo, "trust")
    assert result.exit_code == 0, result.output
    assert result.output == "phil.toml has no [shell] allow, pass_env or bash settings.\n"
    assert not project_policy(calc_repo).exists()


# --- the listing ---------------------------------------------------------------------------------


def lines(result):
    return result.output.splitlines()


def test_the_listing_shows_every_section_in_order(calc_repo, monkeypatch):
    wide(monkeypatch)
    phil(calc_repo, "allow", "gh pr checks *", "--everywhere")
    phil(calc_repo, "deny", "git push *")
    with_shell(calc_repo, 'allow = ["make test *"]\npass_env = ["REPO_TOKEN"]\n')
    result = phil(calc_repo)
    assert result.exit_code == 0, result.output
    out = lines(result)
    assert out[0] == "Built-in: read-only commands (ls, grep, git log, gh pr view, npm ls, …) run without asking."
    everywhere = out.index(f"Everywhere ({shown_path(global_policy_path())}):")
    assert out[everywhere + 1].startswith("  allow  gh pr checks *   manual · ")
    repo = out.index(f"calc ({shown_path(project_policy(calc_repo))}):")
    assert out[repo + 1].startswith("  deny   git push *   manual · ")
    toml = out.index("calc phil.toml (not trusted — phil permissions trust):")
    assert out[toml + 1] == "  allow     make test *   may change things"  # the rows line up (F10)
    assert out[toml + 2] == "  pass_env  REPO_TOKEN"
    config = out.index("Config (built-in defaults (no config entry)):")  # no config.toml sets it (F10)
    assert "  allow  pytest *" in out[config + 1 :]
    assert everywhere < repo < toml < config


def test_the_listing_names_config_toml_when_it_sets_the_allow_list(calc_repo, monkeypatch):
    from phil.config import global_config_path

    wide(monkeypatch)
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[shell]\nallow = ["just *"]\n', encoding="utf-8", newline="\n")
    out = lines(phil(calc_repo))
    config = out.index(f"Config ({shown_path(path)} [shell] allow):")
    assert out[config + 1 :] == ["  allow  just *"]


def test_the_listing_says_when_a_broken_file_switched_trust_off(calc_repo, monkeypatch):
    wide(monkeypatch)
    with_shell(calc_repo, 'allow = ["make test *"]\n')
    phil(calc_repo, "trust", "--yes")
    path = global_policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[[allow]\n", encoding="utf-8", newline="\n")
    assert f"calc phil.toml (trusted, but off until {shown_path(path)} is fixed):" in lines(phil(calc_repo))


def test_the_bash_row_lines_up_too(calc_repo, monkeypatch):
    wide(monkeypatch)
    with_shell(calc_repo, 'allow = ["make test *"]\nbash = "/bin/bash"\n')
    out = lines(phil(calc_repo))
    toml = out.index("calc phil.toml (not trusted — phil permissions trust):")
    assert out[toml + 1 : toml + 3] == ["  allow  make test *   may change things", "  bash   /bin/bash"]


def test_the_listing_leaves_out_empty_sections(calc_repo, monkeypatch):
    wide(monkeypatch)
    result = phil(calc_repo)
    assert result.exit_code == 0, result.output
    assert result.output.startswith("Built-in: ")
    assert "Everywhere" not in result.output
    assert "phil.toml" not in result.output
    assert "Config (" in result.output  # the default [shell] allow


def test_the_listing_shows_the_repo_decision(calc_repo, monkeypatch):
    wide(monkeypatch)
    with_shell(calc_repo, 'allow = ["make test *"]\n')
    phil(calc_repo, "trust", "--yes")
    assert "calc phil.toml (trusted):" in lines(phil(calc_repo))
    phil(calc_repo, "ignore")
    assert "calc phil.toml (ignored):" in lines(phil(calc_repo))


def test_the_listing_shows_a_broken_file_first(calc_repo, monkeypatch):
    wide(monkeypatch)
    phil(calc_repo, "allow", "make test", "--everywhere")
    path = project_policy(calc_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[[allow]]\nrule = "gh * pr"\n', encoding="utf-8", newline="\n")
    result = phil(calc_repo)
    assert result.exit_code == 0, result.output
    first = lines(result)[0]
    assert first.startswith(f"⚠ {shown_path(path)}:2: ")
    assert first.endswith(" — saved allow rules are off until it's fixed")
    assert lines(result)[1].startswith("Built-in: ")


def test_the_listing_shows_hidden_characters_as_question_marks(calc_repo, monkeypatch):
    wide(monkeypatch)
    path = global_policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[[allow]]\nrule = "make test"\nfrom = "evil\\u001b[31m"\n', encoding="utf-8", newline="\n")
    result = phil(calc_repo)
    assert "\x1b" not in result.output
    assert "  allow  make test   evil?[31m" in lines(result)
