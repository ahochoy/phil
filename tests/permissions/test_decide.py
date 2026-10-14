"""ShellPolicy.decide (spec 2026-10-13 §3.1): one ordered decision per command, with a reason and a
label. Policies are real files written by `add_rule` and read back with `load_policy`."""

from datetime import date
from pathlib import Path

import pytest

from phil.agents.tools import CommandLog, make_shell_tool
from phil.config import PhilConfig, ShellConfig
from phil.permissions.load import policy_for
from phil.permissions.store import PolicySet, RepoShellSettings, add_rule, load_policy
from phil.store.paths import ProjectPaths, global_policy_path
from phil.workspace.shell import Decision, ShellPolicy

TODAY = date(2026, 10, 13)


def load(tmp_path: Path, *, config_allow=(), repo_allow=()) -> PolicySet:
    return load_policy(repo_name="calc", project_policy=tmp_path / "proj.toml", global_policy=tmp_path / "global.toml",
                       config_allow=list(config_allow), repo_shell=RepoShellSettings(allow=tuple(repo_allow)))


def decide(policy: ShellPolicy, command: str) -> tuple[str, str, str]:
    decision = policy.decide(command)
    return decision.outcome, decision.reason, decision.label


def test_a_deny_rule_beats_a_read(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "git log *", "manual", today=TODAY)
    decision = ShellPolicy(load(tmp_path)).decide("git log")
    assert (decision.outcome, decision.reason) == ("deny", 'deny rule "git log *" (everywhere)')


def test_a_built_in_read(tmp_path):
    assert decide(ShellPolicy(load(tmp_path)), "gh pr view 1") == ("allow", "read-only (built-in)", "reads only")


def test_a_saved_rule(tmp_path):
    add_rule(tmp_path / "proj.toml", "allow", "gh pr merge *", "manual", today=TODAY)
    decision = ShellPolicy(load(tmp_path)).decide("gh pr merge 1")
    assert (decision.outcome, decision.reason) == ("allow", 'rule "gh pr merge *" (calc)')


def test_a_config_glob(tmp_path):
    decision = ShellPolicy(load(tmp_path, config_allow=["pytest *"])).decide("pytest tests/x.py")
    assert (decision.outcome, decision.reason) == ("allow", 'rule "pytest *" (config)')


def test_the_plans_command(tmp_path):
    decision = ShellPolicy(load(tmp_path), extra_allow=("make check",)).decide("make check -v")
    assert (decision.outcome, decision.reason) == ("allow", "the plan's test command")


def test_an_approval_covers_only_the_exact_command(tmp_path):
    policy = ShellPolicy(load(tmp_path), approved=("rm build.log",))
    decision = policy.decide("rm build.log")
    assert (decision.outcome, decision.reason) == ("allow", "approved for this run")
    assert policy.decide("rm build.log -rf /").outcome == "ask"


def test_nothing_matches(tmp_path):
    assert decide(ShellPolicy(load(tmp_path)), "terraform apply") == ("ask", "no rule matches", "may change things")


def test_forbidden(tmp_path):
    decision = ShellPolicy(load(tmp_path)).decide("/usr/bin/python3 -c x")
    assert decision.outcome == "forbidden"
    assert decision.reason.startswith("forbidden: ")
    assert decision.detail is not None and decision.reason == f"forbidden: {decision.detail}"


def test_a_broken_everywhere_file_switches_saved_allows_off_but_reads_stay(tmp_path):
    (tmp_path / "global.toml").write_text("[[allow]\n", encoding="utf-8", newline="\n")
    add_rule(tmp_path / "proj.toml", "allow", "make test *", "manual", today=TODAY)
    policy = ShellPolicy(load(tmp_path))
    assert policy.decide("make test").outcome == "ask"
    assert policy.decide("gh pr view 1").outcome == "allow"


def test_a_read_only_agent_gets_only_forbidden_checks_and_reads(tmp_path):
    add_rule(tmp_path / "proj.toml", "allow", "make test *", "manual", today=TODAY)
    policy = ShellPolicy(load(tmp_path), read_only=True)
    assert policy.decide("make test").outcome == "ask"
    assert policy.decide("gh pr view 1").outcome == "allow"


def test_a_read_only_agent_ignores_extras_and_approvals(tmp_path):
    policy = ShellPolicy(load(tmp_path), extra_allow=("make check",), approved=("rm x",), read_only=True)
    assert policy.decide("git log").outcome == "allow"
    assert policy.decide("make check").outcome == "ask"
    assert policy.decide("rm x").outcome == "ask"


def test_a_deny_rule_blocks_a_built_in_read_for_a_read_only_agent_too(tmp_path):
    add_rule(tmp_path / "proj.toml", "deny", "gh pr view *", "manual", today=TODAY)
    decision = ShellPolicy(load(tmp_path), read_only=True).decide("gh pr view 1")
    assert (decision.outcome, decision.reason) == ("deny", 'deny rule "gh pr view *" (calc)')


def _trusted(tmp_path: Path, patterns: list[str]) -> PolicySet:
    from phil.permissions.store import record_repo_decision, repo_shell_hash

    settings = RepoShellSettings(allow=tuple(patterns))
    record_repo_decision(tmp_path / "proj.toml", "trust", repo_shell_hash(settings))
    return load(tmp_path, repo_allow=patterns)


@pytest.mark.parametrize(("pattern", "command"), [
    ("uv run pytest *", "uv run pytest -q"),
    ("docker compose run --rm app pytest *", "docker compose run --rm app pytest tests"),
    ("npm run test*", "npm run test:unit"),
])
def test_a_trusted_repo_pattern_matches_as_config_does_r17(tmp_path, pattern, command):
    decision = ShellPolicy(_trusted(tmp_path, [pattern])).decide(command)
    assert (decision.outcome, decision.reason) == ("allow", f'rule "{pattern}" (calc phil.toml)')


@pytest.mark.parametrize(("pattern", "label"), [
    ("git log *", "may change things"),  # an open read reaches more than reads (F5)
    ("cat *", "may change things"),
    ("git remote *", "changes things"),  # `git remote add x url` is a write
    ("git remote -v", "reads only"),
    ("uv run pytest *", "changes things"),
    ("npm run test*", "changes things"),
    ("make test *", "may change things"),
    ("make [ab]", "may change things"),
])
def test_a_repo_pattern_is_labelled_by_its_reach(pattern, label):
    from phil.permissions.load import rule_label

    assert rule_label(pattern) == label


def test_a_deny_rule_beats_a_trusted_repo_pattern(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "uv run pytest *", "manual", today=TODAY)
    assert ShellPolicy(_trusted(tmp_path, ["uv run pytest *"])).decide("uv run pytest -q").outcome == "deny"


def test_a_catalog_read_that_writes_asks_and_is_not_forbidden(tmp_path):
    assert decide(ShellPolicy(load(tmp_path)), "go env -w X=1")[::2] == ("ask", "changes things")


def test_a_deny_rule_beats_an_allow_rule_and_an_approval(tmp_path):
    add_rule(tmp_path / "proj.toml", "deny", "make deploy", "manual", today=TODAY)
    add_rule(tmp_path / "global.toml", "allow", "make *", "manual", today=TODAY)
    decision = ShellPolicy(load(tmp_path), approved=("make deploy",)).decide("make deploy")
    assert (decision.outcome, decision.reason) == ("deny", 'deny rule "make deploy" (calc)')


def test_forbidden_beats_a_deny_rule(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "python3 *", "manual", today=TODAY)
    assert ShellPolicy(load(tmp_path)).decide("python3 -c x").outcome == "forbidden"


def test_containment_applies_to_catalog_reads(tmp_path):
    (tmp_path / "wt").mkdir()
    decision = ShellPolicy(load(tmp_path), root=tmp_path / "wt").decide("git blame ../secret.txt")
    assert decision.outcome == "forbidden"
    assert decision.reason == "forbidden: stay inside the worktree"


def test_a_plain_list_is_config_entries(tmp_path):
    decision = ShellPolicy(["pytest *"]).decide("pytest -q")
    assert (decision.outcome, decision.reason) == ("allow", 'rule "pytest *" (config)')


def test_refusal_detail_is_set_only_for_a_forbidden_command(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "make deploy", "manual", today=TODAY)
    policy = ShellPolicy(load(tmp_path))
    assert [policy.decide(c).outcome for c in ("make deploy", "terraform apply", "ls; rm x", "gh pr view 1")] == [
        "deny", "ask", "forbidden", "allow"]
    assert policy.refusal_detail("make deploy") is None
    assert policy.refusal_detail("ls; rm x") is not None


def test_decision_is_frozen():
    decision = Decision("ask", "no rule matches", "may change things", None)
    with pytest.raises(AttributeError):
        decision.outcome = "allow"  # type: ignore[misc]


def test_the_shell_tool_reports_why_a_command_was_allowed(tmp_path):
    log = CommandLog()
    run_shell = make_shell_tool(tmp_path, ShellConfig(allow=[], timeout_s=10), log, policy=load(tmp_path))
    output = run_shell("git log")
    lines = output.splitlines()
    assert lines[0].startswith("exit_code: ")
    assert lines[1] == "allowed: read-only (built-in)"


def test_the_shell_tool_blocks_a_deny_rule_without_asking(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "git log *", "manual", today=TODAY)
    log = CommandLog()
    run_shell = make_shell_tool(tmp_path, ShellConfig(allow=[], timeout_s=10), log, policy=load(tmp_path))
    output = run_shell("git log")
    assert output == "DENIED: `git log` is blocked by your deny rule \"git log *\" (everywhere). Don't use it."
    assert (log.commands, log.denied, log.refused, log.blocked) == ([], [], [], ["git log"])


def test_policy_for_reads_both_files_config_and_the_repos_list(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("PHIL_HOME", str(home))
    home.mkdir()
    (home / "config.toml").write_text('[shell]\nallow = ["pytest *"]\n', encoding="utf-8", newline="\n")
    repo = tmp_path / "calc"
    repo.mkdir()
    (repo / "phil.toml").write_text('[shell]\nallow = ["make test *"]\n', encoding="utf-8", newline="\n")
    paths = ProjectPaths("calc-1234")
    add_rule(global_policy_path(), "allow", "gh pr merge *", "manual", today=TODAY)
    add_rule(paths.policy_path, "deny", "git push *", "manual", today=TODAY)
    from phil.config import load_config

    policy = policy_for(repo, paths, load_config(repo))
    assert [(e.rule.text, e.scope) for e in policy.allow] == [("gh pr merge *", "everywhere"), ("pytest *", "config")]
    assert [(e.rule.text, e.scope) for e in policy.deny] == [("git push *", "calc")]
    # The repo's own list is not config: it needs trust, and until then it is only reported.
    assert policy.repo_rules_untrusted == ("make test *",)


def test_policy_for_without_a_phil_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    repo = tmp_path / "calc"
    repo.mkdir()
    policy = policy_for(repo, ProjectPaths("calc-1"), PhilConfig())
    assert policy.repo_rules_untrusted == ()
    assert [e.rule.text for e in policy.allow] == PhilConfig().shell.allow


def test_load_config_can_leave_the_repo_out(tmp_path, monkeypatch):
    from phil.config import load_config

    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    (tmp_path / "phil.toml").write_text('[shell]\nallow = ["make *"]\n', encoding="utf-8", newline="\n")
    assert load_config(tmp_path).shell.allow == ["make *"]
    assert load_config(tmp_path, include_repo=False).shell.allow == PhilConfig().shell.allow


# --- Fix round 1 -------------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["git push -f", "/usr/bin/git push x", "GIT push x", "git push --force origin main"])
def test_a_deny_rule_blocks_options_paths_and_case_variants_r13(tmp_path, command):
    add_rule(tmp_path / "global.toml", "deny", "git push *", "manual", today=TODAY)
    decision = ShellPolicy(load(tmp_path, config_allow=["git *"])).decide(command)
    assert (decision.outcome, decision.reason) == ("deny", 'deny rule "git push *" (everywhere)')


def test_a_deny_rule_blocks_a_built_in_read_with_options(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "git log *", "manual", today=TODAY)
    assert ShellPolicy(load(tmp_path)).decide("git log -p").outcome == "deny"


def test_a_deny_rule_blocks_name_value_words(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "make deploy *", "manual", today=TODAY)
    assert ShellPolicy(load(tmp_path, config_allow=["make *"])).decide("make deploy ENV=prod").outcome == "deny"


def test_a_deny_rule_blocks_an_exact_only_command(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "docker run *", "manual", today=TODAY)
    assert ShellPolicy(load(tmp_path, config_allow=["docker *"])).decide("docker run alpine").outcome == "deny"


def test_an_exact_deny_rule_still_needs_the_exact_words(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "make deploy", "manual", today=TODAY)
    policy = ShellPolicy(load(tmp_path))
    assert policy.decide("make deploy").outcome == "deny"
    assert policy.decide("make deploy now").outcome == "ask"
    assert policy.decide("make").outcome == "ask"


@pytest.mark.parametrize(("rule", "command"), [
    ("git push *", "git -C x push -f"),
    ("git push *", "git --git-dir=y push x"),
    ("gh pr merge *", "gh --repo o/r pr merge 1"),
])
def test_a_deny_rule_finds_its_words_after_global_options(tmp_path, rule, command):
    add_rule(tmp_path / "global.toml", "deny", rule, "manual", today=TODAY)
    decision = ShellPolicy(load(tmp_path, config_allow=["git *", "gh *"])).decide(command)
    assert (decision.outcome, decision.reason) == ("deny", f'deny rule "{rule}" (everywhere)')


def test_an_open_deny_rule_needs_all_its_words_in_order(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "git push *", "manual", today=TODAY)
    policy = ShellPolicy(load(tmp_path))
    assert policy.decide("git log").outcome == "allow"


def test_deny_matches_order_and_exactness():
    from phil.permissions.rules import deny_matches, parse_rule

    assert deny_matches(parse_rule("gh pr merge *"), ["gh", "--repo", "o/r", "pr", "x", "merge"])
    assert not deny_matches(parse_rule("gh pr merge *"), ["gh", "merge", "pr"])  # out of order
    assert not deny_matches(parse_rule("make deploy"), ["make", "deploy", "x"])
    assert not deny_matches(parse_rule("make deploy"), ["make", "-k", "deploy"])
    assert deny_matches(parse_rule("MAKE deploy"), ["/usr/bin/make", "deploy"])


def _git_setup(tmp_path: Path, monkeypatch):
    """Config allows `git *` (the global file), the everywhere policy denies `git push *`."""
    from phil.config import load_config

    home = tmp_path / "home"
    monkeypatch.setenv("PHIL_HOME", str(home))
    home.mkdir()
    (home / "config.toml").write_text('[shell]\nallow = ["git *"]\n', encoding="utf-8", newline="\n")
    add_rule(global_policy_path(), "deny", "git push *", "manual", today=TODAY)
    repo = _repo(tmp_path, "")
    return repo, load_config(repo)


def test_a_deeply_nested_phil_toml_keeps_the_deny_rules(tmp_path, monkeypatch):
    repo, config = _git_setup(tmp_path, monkeypatch)
    # Broken after config was loaded (a pull during a long run): tomllib raises RecursionError.
    (repo / "phil.toml").write_text("x = " + "[" * 5000 + "]" * 5000 + "\n", encoding="utf-8", newline="\n")
    from phil.permissions.load import policy_or_fallback

    policy = policy_or_fallback(repo, ProjectPaths("calc-1"), config)
    assert ShellPolicy(policy).decide("git push -f").outcome == "deny"
    assert any("phil.toml" in problem.message for problem in policy.problems)


@pytest.mark.parametrize("target", ["repo_shell_settings", "_config_allow_for"])
def test_any_failure_reading_the_repo_or_config_keeps_the_deny_rules(tmp_path, monkeypatch, target):
    from phil.permissions import load as load_module

    repo, config = _git_setup(tmp_path, monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(load_module, target, boom)
    policy = load_module.policy_or_fallback(repo, ProjectPaths("calc-1"), config)
    assert ShellPolicy(policy).decide("git push -f").outcome == "deny"
    assert policy.problems


def test_when_load_policy_itself_fails_nothing_is_allowed(tmp_path, monkeypatch, caplog):
    from phil.permissions import load as load_module

    repo, config = _git_setup(tmp_path, monkeypatch)

    def boom(**kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(load_module, "load_policy", boom)
    policy = load_module.policy_or_fallback(repo, ProjectPaths("calc-1"), config)
    assert policy.allow == ()
    assert ShellPolicy(policy).decide("git push -f").outcome == "ask"  # asks rather than runs
    assert ShellPolicy(policy).decide("gh pr view 1").outcome == "allow"  # built-in reads stay
    assert policy.problems
    assert "couldn't load the permission rules" in caplog.text


def test_load_config_turns_a_recursion_error_into_a_config_error(tmp_path, monkeypatch):
    from phil.config import ConfigError, load_config

    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    (tmp_path / "phil.toml").write_text("x = " + "[" * 5000 + "]" * 5000 + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(ConfigError, match="phil.toml"):
        load_config(tmp_path)


def test_deny_matches_unit():
    from phil.permissions.rules import deny_matches, parse_rule

    rule = parse_rule("git push *")
    assert deny_matches(rule, ["git", "push"])
    assert deny_matches(rule, ["/usr/bin/git", "push", "-f", "X=1"])
    assert not deny_matches(rule, ["git", "pull"])
    assert not deny_matches(rule, ["git"])
    assert not deny_matches(rule, [])
    assert deny_matches(parse_rule("./gh pr *"), ["/tmp/gh", "pr", "merge"])  # by name, whatever the path


def test_a_denied_or_refused_read_is_labelled_reads_only(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "git log *", "manual", today=TODAY)
    (tmp_path / "wt").mkdir()
    policy = ShellPolicy(load(tmp_path), root=tmp_path / "wt")
    assert policy.decide("git log").label == "reads only"
    refused = policy.decide("cat /etc/passwd")
    assert (refused.outcome, refused.label) == ("forbidden", "reads only")


def test_the_ask_message_lists_at_most_ten_patterns(tmp_path):
    patterns = [f"tool{i} *" for i in range(13)]
    run_shell = make_shell_tool(tmp_path, ShellConfig(allow=[], timeout_s=10), CommandLog(),
                                policy=load(tmp_path, config_allow=patterns))
    output = run_shell("terraform apply")
    assert output == ("DENIED: `terraform apply` is not on the allowlist. Allowed patterns: "
                      + ", ".join(patterns[:10]) + " … (3 more: phil permissions)")


def _repo(tmp_path: Path, phil_toml: str) -> Path:
    repo = tmp_path / "calc"
    repo.mkdir()
    (repo / "phil.toml").write_text(phil_toml, encoding="utf-8", newline="\n")
    return repo


def test_a_broken_phil_toml_keeps_the_deny_rules_and_reports_a_problem(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path, "[shell\n")
    add_rule(global_policy_path(), "deny", "make deploy *", "manual", today=TODAY)
    config = PhilConfig.model_validate({"shell": {"allow": ["make *"]}})
    config._sources = {"shell.allow": "phil.toml"}  # as load_config left it, before phil.toml broke
    policy = policy_for(repo, ProjectPaths("calc-1"), config)
    assert ShellPolicy(policy).decide("make deploy x").outcome == "deny"
    assert [e.rule.text for e in policy.allow] == PhilConfig().shell.allow  # defaults: no repo list
    assert policy.repo_rules_untrusted == ()
    assert any("phil.toml" in problem.message for problem in policy.problems)


def test_a_config_that_fails_to_reload_keeps_the_deny_rules_and_reports_a_problem(tmp_path, monkeypatch):
    from phil.config import load_config

    home = tmp_path / "home"
    monkeypatch.setenv("PHIL_HOME", str(home))
    repo = _repo(tmp_path, '[shell]\nallow = ["make *"]\n')
    config = load_config(repo)
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text("[shell\n", encoding="utf-8", newline="\n")  # broken since
    add_rule(global_policy_path(), "deny", "make deploy *", "manual", today=TODAY)
    policy = policy_for(repo, ProjectPaths("calc-1"), config)
    assert ShellPolicy(policy).decide("make deploy").outcome == "deny"
    assert [e.rule.text for e in policy.allow] == PhilConfig().shell.allow
    assert policy.problems


def test_policy_for_honours_set_over_a_repo_list(tmp_path, monkeypatch):
    from phil.config import load_config

    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path, '[shell]\nallow = ["make *"]\n')
    policy = policy_for(repo, ProjectPaths("calc-1"), load_config(repo, overrides=['shell.allow=["just *"]']))
    assert [(e.rule.text, e.scope) for e in policy.allow] == [("just *", "config")]
    assert policy.repo_rules_untrusted == ("make *",)


def test_the_fallback_drops_a_repo_set_list(tmp_path, monkeypatch, caplog):
    from phil.config import load_config
    from phil.permissions import load as load_module

    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path, '[shell]\nallow = ["make *"]\n')

    def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(load_module, "policy_for", broken)
    policy = load_module.policy_or_fallback(repo, ProjectPaths("calc-1"), load_config(repo))
    assert policy.allow == () and policy.deny == ()
    assert "couldn't load the permission rules" in caplog.text


def test_the_repos_shell_settings_are_reported_not_applied_r14(tmp_path, monkeypatch):
    from phil.config import load_config
    from phil.permissions.load import trusted_shell
    from phil.permissions.store import RepoShellSettings

    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path, '[shell]\nallow = ["make *"]\npass_env = ["AWS_SECRET_ACCESS_KEY"]\nbash = "C:/evil/bash.exe"\n')
    config = load_config(repo)
    policy = policy_for(repo, ProjectPaths("calc-1"), config)
    assert policy.repo_shell == RepoShellSettings(
        allow=("make *",), pass_env=("AWS_SECRET_ACCESS_KEY",), bash="C:/evil/bash.exe")
    shell = trusted_shell(config, repo)
    assert (shell.allow, shell.pass_env, shell.bash) == (PhilConfig().shell.allow, [], None)
    assert shell.timeout_s == config.shell.timeout_s


def test_trusted_shell_keeps_set_and_global_values(tmp_path, monkeypatch):
    from phil.config import load_config
    from phil.permissions.load import trusted_shell

    home = tmp_path / "home"
    monkeypatch.setenv("PHIL_HOME", str(home))
    home.mkdir()
    (home / "config.toml").write_text('[shell]\npass_env = ["MY_TOKEN"]\n', encoding="utf-8", newline="\n")
    repo = _repo(tmp_path, '[shell]\npass_env = ["AWS_SECRET_ACCESS_KEY"]\n')
    assert trusted_shell(load_config(repo), repo).pass_env == ["MY_TOKEN"]
    with_set = load_config(repo, overrides=['shell.pass_env=["X_KEY"]'])
    assert trusted_shell(with_set, repo).pass_env == ["X_KEY"]
