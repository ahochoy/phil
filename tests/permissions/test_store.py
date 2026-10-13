import threading
from datetime import date
from pathlib import Path

import pytest

from phil.permissions.store import (
    PolicyWriteError, add_rule, load_policy, record_repo_decision, remove_rule, repo_decision, repo_rules_hash,
)
from phil.store.paths import ProjectPaths, global_policy_path

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


def test_a_new_file_starts_with_the_header_comment(tmp_path):
    path = tmp_path / "global.toml"
    add_rule(path, "allow", "npm ls", "manual", today=TODAY, header="# Phil permissions — everywhere.")
    add_rule(path, "deny", "git push *", "manual", today=TODAY, header="# ignored: the file exists")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Phil permissions — everywhere.\n") and "ignored" not in text
    assert len(load(tmp_path).allow) == 1


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


def test_the_hash_tells_rule_lists_apart():
    assert repo_rules_hash(["make test", "ls"]) != repo_rules_hash(["make test\nls"])
    assert repo_rules_hash(["a *", "b *"]) != repo_rules_hash(["b *", "a *"])


def test_spacing_variants_of_a_rule_are_one_rule_stored_normalised(tmp_path):
    path = tmp_path / "global.toml"
    add_rule(path, "allow", "make  test *", "manual", today=TODAY)
    add_rule(path, "allow", "make test *", "manual", today=TODAY)
    add_rule(path, "allow", "  make test   *  ", "manual", today=TODAY)
    text = path.read_text(encoding="utf-8")
    assert text.count("[[allow]]") == 1 and 'rule = "make test *"' in text


def test_a_new_entry_in_an_existing_list_gets_a_blank_line_before_it(tmp_path):
    path = tmp_path / "global.toml"
    add_rule(path, "allow", "npm ls", "manual", today=TODAY)
    add_rule(path, "allow", "make test *", "manual", today=TODAY)
    assert '"manual"\n\n[[allow]]\nrule = "make test *"' in path.read_text(encoding="utf-8")


def test_an_invalid_allow_entry_leaves_a_valid_deny_rule_in_force(tmp_path):
    (tmp_path / "proj.toml").write_text(
        '[[allow]]\nrule = "gh * view"\n\n[[deny]]\nrule = "git push *"\n', encoding="utf-8", newline="\n")
    policy = load(tmp_path)
    assert policy.problems and policy.allow == ()
    assert [e.rule.text for e in policy.deny] == ["git push *"]


def test_a_stale_tmp_file_is_overwritten(tmp_path):
    path = tmp_path / "global.toml"
    (tmp_path / "global.toml.tmp").write_text("garbage = [", encoding="utf-8", newline="\n")
    add_rule(path, "allow", "npm ls", "manual", today=TODAY)
    assert [e.rule.text for e in load(tmp_path).allow] == ["npm ls"]
    assert not (tmp_path / "global.toml.tmp").exists()


def test_policy_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path))
    assert global_policy_path() == tmp_path / "policy.toml"
    assert ProjectPaths("calc").policy_path == tmp_path / "projects" / "calc" / "policy.toml"
