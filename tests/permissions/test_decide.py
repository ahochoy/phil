"""ShellPolicy.decide (spec 2026-10-13 §3.1): one ordered decision per command, with a reason and a
label. Policies are real files written by `add_rule` and read back with `load_policy`."""

from datetime import date
from pathlib import Path

import pytest

from phil.agents.tools import CommandLog, make_shell_tool
from phil.config import PhilConfig, ShellConfig
from phil.permissions.load import policy_for
from phil.permissions.store import PolicySet, add_rule, load_policy
from phil.store.paths import ProjectPaths, global_policy_path
from phil.workspace.shell import Decision, ShellPolicy

TODAY = date(2026, 10, 13)


def load(tmp_path: Path, *, config_allow=(), repo_allow=()) -> PolicySet:
    return load_policy(repo_name="calc", project_policy=tmp_path / "proj.toml", global_policy=tmp_path / "global.toml",
                       config_allow=list(config_allow), repo_allow=list(repo_allow))


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


def test_a_read_only_agent_ignores_deny_rules_extras_and_approvals(tmp_path):
    add_rule(tmp_path / "proj.toml", "deny", "git log *", "manual", today=TODAY)
    policy = ShellPolicy(load(tmp_path), extra_allow=("make check",), approved=("rm x",), read_only=True)
    assert policy.decide("git log").outcome == "allow"
    assert policy.decide("make check").outcome == "ask"
    assert policy.decide("rm x").outcome == "ask"


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


def test_denial_reason_wraps_decide(tmp_path):
    add_rule(tmp_path / "global.toml", "deny", "make deploy", "manual", today=TODAY)
    policy = ShellPolicy(load(tmp_path))
    assert policy.denial_reason("make deploy") == "denied"
    assert policy.denial_reason("terraform apply") == "not_allowed"
    assert policy.denial_reason("ls; rm x") == "forbidden"
    assert policy.denial_reason("gh pr view 1") is None
    assert policy.refusal_detail("make deploy") is None
    assert not policy.is_allowed("make deploy")


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
    assert (log.commands, log.denied, log.refused) == ([], [], [])


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
