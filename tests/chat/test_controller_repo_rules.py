"""Trusting a repo's phil.toml shell settings (spec 2026-10-13 §3.5, R14): the chat asks once per
hash, before plan approval and before a quick run starts, and records the answer in the repo's
policy file."""

from phil.chat.decision import repo_rules_decision
from phil.config import load_config
from phil.permissions.load import policy_for, repo_shell_settings, trusted_shell
from phil.permissions.store import repo_decision, repo_shell_hash
from phil.repo import resolve_repo
from phil.store.paths import ProjectPaths
from phil.workspace.shell import ShellPolicy
from tests.chat.test_controller import FULL_SCRIPT, run_chat
from tests.chat.test_quick_flow import QUICK_LINE, detectable, quick_goal
from tests.chat.test_routing_flow import route
from tests.helpers import MODELS_TOML

TITLE = "calc's phil.toml allows these commands"


def with_shell(repo, shell: str):
    (repo / "phil.toml").write_text(MODELS_TOML + "\n[shell]\n" + shell, encoding="utf-8", newline="\n")
    return repo


def chooses(calls):
    return [call[2] for call in calls if call[0] == "choose"]


def titles(calls):
    return [decision.title for decision in chooses(calls)]


def recorded(repo):
    """The decision recorded for the repo's current [shell] settings, or None."""
    paths = ProjectPaths(resolve_repo(repo).slug)
    return repo_decision(paths.policy_path, repo_shell_hash(repo_shell_settings(repo)))


def plan_chat(repo, answers, calls):
    return run_chat(repo, ["add subtract", *answers], dict(FULL_SCRIPT), choose=True, calls=calls)


def test_the_first_plan_with_repo_rules_asks_before_approval_and_records_trust(calc_repo):
    with_shell(calc_repo, 'allow = ["make test *"]\n')
    calls = []
    text, spawned, *_ = plan_chat(calc_repo, ["trust", "y"], calls)
    assert titles(calls) == [TITLE, "Approve this plan?"]
    trust = chooses(calls)[0]
    assert [(o.label, o.answer) for o in trust.options] == [("Trust these for calc", "trust"), ("Ignore them", "ignore")]
    assert any(line.startswith("make test *") and line.endswith("may change things") for line in trust.body)
    assert "✓ Trust these for calc" in text
    assert recorded(calc_repo) == "trust"
    assert len(spawned) == 1
    info = resolve_repo(calc_repo)
    policy = policy_for(info.root, ProjectPaths(info.slug), load_config(info.root))
    assert ShellPolicy(policy).decide("make test").reason == 'rule "make test *" (calc phil.toml)'


def test_the_next_plan_asks_nothing_even_in_another_chat(calc_repo):
    with_shell(calc_repo, 'allow = ["make test *"]\n')
    plan_chat(calc_repo, ["trust", "n"], [])
    calls = []
    plan_chat(calc_repo, ["n"], calls)
    assert titles(calls) == ["Approve this plan?"]


def test_a_changed_list_asks_again(calc_repo):
    with_shell(calc_repo, 'allow = ["make test *"]\n')
    plan_chat(calc_repo, ["trust", "n"], [])
    with_shell(calc_repo, 'allow = ["make test *", "make deploy *"]\n')
    assert recorded(calc_repo) is None
    calls = []
    plan_chat(calc_repo, ["trust", "n"], calls)
    assert titles(calls) == [TITLE, "Approve this plan?"]
    assert any(line.startswith("make deploy *") for line in chooses(calls)[0].body)
    assert recorded(calc_repo) == "trust"


def test_ignore_keeps_the_settings_off_and_later_plans_ask_nothing(calc_repo, monkeypatch):
    monkeypatch.setenv("REPO_TOKEN", "s3cret")
    with_shell(calc_repo, 'allow = ["make test *"]\npass_env = ["REPO_TOKEN"]\n')
    text, *_ = plan_chat(calc_repo, ["ignore", "n"], [])
    assert "✓ Ignore them" in text
    assert recorded(calc_repo) == "ignore"
    info = resolve_repo(calc_repo)
    config = load_config(info.root)
    policy = policy_for(info.root, ProjectPaths(info.slug), config)
    assert ShellPolicy(policy).decide("make test").outcome == "ask"
    assert policy.repo_rules_untrusted == ()
    assert trusted_shell(config, info.root, policy).pass_env == []
    calls = []
    plan_chat(calc_repo, ["n"], calls)
    assert titles(calls) == ["Approve this plan?"]


def test_the_quick_path_asks_too(calc_repo):
    detectable(calc_repo)
    with_shell(calc_repo, 'allow = ["make test *"]\n')
    calls = []
    text, spawned, *_ = run_chat(
        calc_repo, ["fix the typo in calc", "trust"],
        {"route": [route("simple_change")], "intake": [quick_goal()]}, choose=True, calls=calls,
    )
    assert titles(calls) == [TITLE]
    assert text.index("✓ Trust these for calc") < text.index(QUICK_LINE)
    assert recorded(calc_repo) == "trust"
    assert len(spawned) == 1


def test_no_repo_settings_no_callout(calc_repo):
    calls = []
    plan_chat(calc_repo, ["n"], calls)
    assert titles(calls) == ["Approve this plan?"]


def test_a_phil_toml_changed_while_asking_records_nothing_and_asks_again(calc_repo):
    with_shell(calc_repo, 'allow = ["make test *"]\n')
    shown = repo_shell_hash(repo_shell_settings(calc_repo))

    def change_then_trust(controller):
        with_shell(calc_repo, 'allow = ["make deploy *"]\n')
        return "trust"

    calls = []
    text, *_ = plan_chat(calc_repo, [change_then_trust, "trust", "n"], calls)
    assert titles(calls) == [TITLE, TITLE, "Approve this plan?"]
    first, second = chooses(calls)[:2]
    assert any(line.startswith("make test *") for line in first.body)
    assert any(line.startswith("make deploy *") for line in second.body)
    assert "phil.toml changed" in text
    paths = ProjectPaths(resolve_repo(calc_repo).slug)
    assert repo_decision(paths.policy_path, shown) is None
    assert recorded(calc_repo) == "trust"  # what was shown the second time


def test_pass_env_and_bash_are_shown_and_trusted_with_the_rules(calc_repo):
    with_shell(calc_repo, 'pass_env = ["REPO_TOKEN", "NPM_TOKEN"]\nbash = "C:/Git/bin/bash.exe"\n')
    calls = []
    plan_chat(calc_repo, ["trust", "n"], calls)
    trust = chooses(calls)[0]
    assert trust.title == TITLE
    assert "passes these environment variables to agent commands: REPO_TOKEN, NPM_TOKEN" in trust.body
    assert "runs every command through: C:/Git/bin/bash.exe" in trust.body
    info = resolve_repo(calc_repo)
    config = load_config(info.root)
    policy = policy_for(info.root, ProjectPaths(info.slug), config)
    shell = trusted_shell(config, info.root, policy)
    assert (shell.pass_env, shell.bash) == (["REPO_TOKEN", "NPM_TOKEN"], "C:/Git/bin/bash.exe")


def test_the_decision_shows_hidden_characters_as_question_marks():
    decision = repo_rules_decision(
        "ca\x1blc", ["make \x1b[31mtest *"], ["may change things"], pass_env=["A\u202eB"], bash="C:/b\tash.exe")
    assert decision.title == "ca?lc's phil.toml allows these commands"
    assert decision.options[0].label == "Trust these for ca?lc"
    text = "\n".join(decision.body)
    assert "\x1b" not in text and "\u202e" not in text and "\t" not in text
    assert any(line.startswith("make ?[31mtest *") for line in decision.body)
    assert "passes these environment variables to agent commands: A?B" in decision.body
    assert "runs every command through: C:/b?ash.exe" in decision.body
