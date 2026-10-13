import json
import shlex
import sys
from datetime import date

from phil.permissions.store import add_rule
from phil.store.paths import global_policy_path

from tests.helpers import run_git
from tests.run.conftest import review, task_result, tester_report, write_green, write_red

BUILD = f"{shlex.quote(sys.executable)} tools/build.py"


def add_build_script(repo):
    (repo / "tools").mkdir()
    (repo / "tools" / "build.py").write_text("print('built ok')\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "add build script")


def red_with_build(outputs):
    def script(turn):
        outputs.append(turn.tools["run_shell"](BUILD))
        return write_red(turn)

    return script


def test_denied_command_escalates_then_approval_allows_it(make_harness, calc_repo):
    add_build_script(calc_repo)
    outputs: list[str] = []
    harness = make_harness(
        {
            "implementer": [red_with_build(outputs), red_with_build(outputs), write_green],
            "tester": [tester_report()],
            "reviewer": [review()],
        }
    )
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "approval"
    assert escalation["commands"] == [BUILD]
    assert outputs[0].startswith("DENIED:")

    final = harness.resume({"action": "approve"})
    assert final["status"] == "completed"
    assert outputs[1].startswith("exit_code: 0")
    assert "built ok" in outputs[1]
    assert final["approved"] == [BUILD]
    assert final["attempts"] == 0


def test_deny_continues_to_verify_with_a_hint(make_harness, calc_repo):
    add_build_script(calc_repo)
    outputs: list[str] = []
    harness = make_harness(
        {"implementer": [red_with_build(outputs), write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    harness.start()
    final = harness.resume({"action": "deny"})
    assert final["status"] == "completed"
    green_packet = [p for role, p in harness.factory.calls if role == "implementer"][-1]["messages"][0]["content"]
    # The hint reaches the packet inside its JSON input, so a Windows path's backslashes are
    # JSON-escaped there; json.dumps gives the same escaping (and none for a POSIX path).
    hint = f"Not approved: {BUILD}. Do not use them."
    assert json.dumps(hint)[1:-1] in green_packet


REFUSED_CMD = "python -c 'print(1)'"


def test_refused_command_does_not_escalate(make_harness):
    outputs: list[str] = []

    def refuse_then_red(turn):
        outputs.append(turn.tools["run_shell"](REFUSED_CMD))
        return write_red(turn)

    harness = make_harness(
        {"implementer": [refuse_then_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert "__interrupt__" not in final
    assert final["status"] == "completed"
    assert outputs[0].startswith("REFUSED:")


def test_read_only_command_does_not_escalate(make_harness):
    outputs: list[str] = []

    def ls_then_red(turn):
        outputs.append(turn.tools["run_shell"]("ls src"))
        return write_red(turn)

    harness = make_harness(
        {"implementer": [ls_then_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert "__interrupt__" not in final
    assert final["status"] == "completed"
    assert not outputs[0].startswith(("DENIED:", "REFUSED:"))


def test_refused_command_reaches_the_next_attempt_as_feedback(make_harness):
    def refuse_only(turn):
        turn.tools["run_shell"](REFUSED_CMD)
        return task_result("red")

    harness = make_harness(
        {"implementer": [refuse_only, write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert final["status"] == "completed"
    second = [p for role, p in harness.factory.calls if role == "implementer"][1]["messages"][0]["content"]
    assert "refused command: python -c 'print(1)'" in second


def test_a_deny_rule_blocks_without_escalating_and_reaches_the_next_attempt(make_harness):
    add_rule(global_policy_path(), "deny", "make deploy *", "manual", today=date(2026, 10, 13))
    outputs: list[str] = []

    def blocked_only(turn):
        outputs.append(turn.tools["run_shell"]("make deploy now"))
        return task_result("red")

    harness = make_harness(
        {"implementer": [blocked_only, write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert "__interrupt__" not in final
    assert final["status"] == "completed"
    assert outputs[0] == 'DENIED: `make deploy now` is blocked by your deny rule "make deploy *" (everywhere). Don\'t use it.'
    second = [p for role, p in harness.factory.calls if role == "implementer"][1]["messages"][0]["content"]
    assert "blocked by your rule: make deploy now" in second


def test_blocked_and_refused_commands_reach_the_summary_even_when_the_attempt_passes(make_harness):
    add_rule(global_policy_path(), "deny", "make deploy *", "manual", today=date(2026, 10, 13))

    def blocked_then_red(turn):
        turn.tools["run_shell"]("make deploy now")
        turn.tools["run_shell"](REFUSED_CMD)
        return write_red(turn)

    harness = make_harness(
        {"implementer": [blocked_then_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert final["status"] == "completed"
    summary = (harness.deps.artifacts.run_dir / "summary.md").read_text(encoding="utf-8")
    assert "blocked by your rule: make deploy now" in summary
    assert f"refused command: {REFUSED_CMD}" in summary


def build_only(turn):
    turn.tools["run_shell"](BUILD)
    return task_result("red")  # writes nothing


def build_only_after_approval(turn):
    turn.tools["run_shell"](BUILD)  # approved now; still writes nothing
    return task_result("red")


def red_unchanged(turn):
    return task_result("red", ["tests/test_sub.py"], ["tests/test_sub.py"])  # continues; writes nothing more


def test_an_approval_resume_that_changed_nothing_since_the_task_began_fails(make_harness, calc_repo):
    from phil.config import PhilConfig

    add_build_script(calc_repo)
    config = PhilConfig.model_validate({"run": {"max_attempts_per_phase": 1}})
    harness = make_harness({"implementer": [build_only, build_only_after_approval]}, config=config)
    assert harness.start()["__interrupt__"][0].value["reason"] == "approval"

    escalation = harness.resume({"action": "approve"})["__interrupt__"][0].value
    assert escalation["reason"] == "attempts"
    assert escalation["problems"] == ["no changes were made for CALC-001"]


def test_an_approval_resume_keeps_what_the_paused_call_wrote(make_harness, calc_repo):
    add_build_script(calc_repo)
    outputs: list[str] = []
    harness = make_harness({
        "implementer": [red_with_build(outputs), red_unchanged, write_green],
        "tester": [tester_report()],
        "reviewer": [review()],
    })
    assert harness.start()["__interrupt__"][0].value["reason"] == "approval"

    final = harness.resume({"action": "approve"})
    assert final["status"] == "completed"
    assert harness.factory.remaining() == {"implementer": 0, "tester": 0, "reviewer": 0}


# --- "Always allow": the callout saves rules (spec 2026-10-13 §3.3, §3.4) ---------------------

# The brief's `make test-unit --fast` predates R9: an option after the subcommand is saved exactly
# (see test_an_option_is_suggested_exactly), so the open-ended case uses a plain argument.
MERGE, UNIT = "gh pr merge 42", "make test-unit calc"


def asks(*commands):
    """An implementer call that tries `commands` (each is denied: no rule allows it), then writes red."""
    def script(turn):
        for command in commands:
            assert turn.tools["run_shell"](command).startswith("DENIED:")
        return write_red(turn)

    return script


def paused(make_harness, *commands):
    """A harness whose run paused for approval of `commands`, and that escalation."""
    harness = make_harness({"implementer": [asks(*commands)]})
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "approval"
    return harness, escalation


def answer(harness, monkeypatch, action):
    """Resume the paused run's escalate node with `action` (and the pause's id, as the chat sends
    it), returning what it hands the graph."""
    state = harness.graph.get_state(harness.thread).values

    def interrupt(payload):
        assert "error" not in payload, payload["error"]  # the engine rejected the answer
        return {"action": action, "id": state["escalation"].get("id")}

    monkeypatch.setattr("phil.run.engine.interrupt", interrupt)
    return harness.engine.escalate(state)


def feed_lines(harness, kind="rule_saved"):
    from phil.ui.feed_view import FeedRenderer

    events, _ = harness.deps.events.read()
    return [FeedRenderer().milestone(event, 200).plain for event in events if event["kind"] == kind]


def test_an_approval_escalation_suggests_rules_and_labels_each_command(make_harness):
    _, escalation = paused(make_harness, MERGE, UNIT)
    assert escalation["commands"] == [MERGE, UNIT]
    assert escalation["labels"] == ["changes things", "may change things"]
    assert escalation["rules"] == ["gh pr merge *", "make test-unit *"]
    assert escalation["options"] == ["approve", "always_repo", "always_everywhere", "deny", "abort"]
    assert escalation["repo"] == "calc"


def test_an_option_is_suggested_exactly(make_harness):
    _, escalation = paused(make_harness, "make test-unit --fast")
    assert escalation["rules"] == ["make test-unit --fast"]
    assert escalation["labels"] == ["may change things"]
    assert escalation["options"] == ["approve", "always_repo", "always_everywhere", "deny", "abort"]


def test_the_same_rule_is_suggested_once(make_harness):
    _, escalation = paused(make_harness, "gh pr merge 42", "gh pr merge 43")
    assert escalation["rules"] == ["gh pr merge *"]
    assert escalation["labels"] == ["changes things", "changes things"]


def test_an_interpreter_is_never_offered_everywhere(make_harness):
    _, escalation = paused(make_harness, MERGE, "python3 x.py")
    assert escalation["rules"] == ["gh pr merge *", "python3 x.py"]
    assert escalation["labels"] == ["changes things", "changes things"]
    assert escalation["options"] == ["approve", "always_repo", "deny", "abort"]


def test_a_command_no_rule_can_express_offers_no_always(make_harness):
    # R4: a literal `*` word can't be saved as a rule, so neither "always" answer is offered.
    _, escalation = paused(make_harness, MERGE, "make test '*'")
    assert escalation["options"] == ["approve", "deny", "abort"]


def test_always_repo_saves_the_rules_and_approves_for_the_run(make_harness, monkeypatch):
    import tomllib

    harness, _ = paused(make_harness, MERGE, UNIT)
    result = answer(harness, monkeypatch, "always_repo")
    assert result["approved"] == [MERGE, UNIT]
    assert result["next"] == "implement" and result["keep_worktree"] is True
    assert result["escalation"] is None and result["denied"] == []

    path = harness.deps.paths.policy_path
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Phil permissions — calc. `phil permissions` shows what's in effect.\n")
    saved = tomllib.loads(text)["allow"]
    assert [entry["rule"] for entry in saved] == ["gh pr merge *", "make test-unit *"]
    assert {entry["from"] for entry in saved} == {"approval in calc (r-0001)"}
    assert not global_policy_path().exists()
    assert feed_lines(harness) == ['Saved rules "gh pr merge *", "make test-unit *" for calc · /permissions to review']


def test_always_everywhere_saves_to_the_global_file(make_harness, monkeypatch, tmp_path):
    import tomllib

    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    harness, _ = paused(make_harness, MERGE)
    result = answer(harness, monkeypatch, "always_everywhere")
    assert result["approved"] == [MERGE]

    path = tmp_path / "home" / "policy.toml"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Phil permissions — everywhere. `phil permissions` shows what's in effect.\n")
    assert [entry["rule"] for entry in tomllib.loads(text)["allow"]] == ["gh pr merge *"]
    assert not harness.deps.paths.policy_path.exists()
    assert feed_lines(harness) == ['Saved rule "gh pr merge *" for everywhere · /permissions to review']


def test_a_rule_that_cannot_be_saved_still_approves_for_the_run(make_harness, monkeypatch):
    harness, _ = paused(make_harness, MERGE)
    harness.deps.paths.policy_path.write_text("this is [not toml\n", encoding="utf-8", newline="\n")
    result = answer(harness, monkeypatch, "always_repo")
    assert result["approved"] == [MERGE]
    assert result["next"] == "implement"
    [line] = feed_lines(harness)
    assert line.startswith("Couldn't save rule: ")
    assert line.endswith(" · allowed for this run only")
    assert harness.deps.paths.policy_path.read_text(encoding="utf-8") == "this is [not toml\n"


def test_a_saved_rule_allows_the_command_on_the_next_call(make_harness, monkeypatch):
    harness, _ = paused(make_harness, UNIT)
    answer(harness, monkeypatch, "always_repo")
    from phil.permissions.load import policy_for
    from phil.workspace.shell import ShellPolicy

    policy = policy_for(harness.deps.repo_root, harness.deps.paths, harness.deps.config)
    assert ShellPolicy(policy).decide("make test-unit other").reason == 'rule "make test-unit *" (calc)'


def test_each_command_carries_the_rule_it_would_save(make_harness):
    _, escalation = paused(make_harness, MERGE, "make test '*'")
    assert escalation["saves"] == ["gh pr merge *", None]


def test_a_command_whose_rule_cannot_be_parsed_offers_no_always(make_harness):
    # M1: a tab inside a quoted word is suggested, but parse_rule rejects the control character.
    _, escalation = paused(make_harness, 'git commit -m "a\tb"')
    assert escalation["saves"] == [None]
    assert escalation["options"] == ["approve", "deny", "abort"]


def test_the_rule_shown_is_the_rule_saved_on_windows(monkeypatch):
    """Windows simulated (add_rule's file lock needs real Windows, so the stored text is checked
    with normalised_rule, which is exactly what add_rule stores)."""
    from phil import platform
    from phil.permissions.rules import suggest_rule
    from phil.permissions.store import normalised_rule
    from phil.run.engine import saveable_rule

    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    for argv, shown in ((["GH.EXE", "pr", "view", "1"], "gh pr view *"), (["make", "sub\\dir"], "make sub/dir *")):
        assert suggest_rule(argv) != shown  # the raw suggestion is not what add_rule would store
        assert saveable_rule(argv) == shown
        assert normalised_rule(shown) == shown  # saving the shown text stores it unchanged


def test_a_partial_save_names_what_was_saved(make_harness, monkeypatch):
    from phil.permissions.store import PolicyWriteError
    from phil.run import engine

    real = engine.add_rule

    def add_rule(path, kind, rule, *args, **kwargs):
        if rule == "make test-unit *":
            raise PolicyWriteError("disk full")
        return real(path, kind, rule, *args, **kwargs)

    harness, _ = paused(make_harness, MERGE, UNIT)
    monkeypatch.setattr(engine, "add_rule", add_rule)
    result = answer(harness, monkeypatch, "always_repo")
    assert result["approved"] == [MERGE, UNIT]
    assert feed_lines(harness) == [
        'Saved rule "gh pr merge *" for calc; couldn\'t save "make test-unit *": disk full'
        " · make test-unit calc allowed for this run only"
    ]


def test_each_pause_has_its_own_id(make_harness):
    harness = make_harness({"implementer": [asks(MERGE), asks("npm install left-pad")]})
    first = harness.start()["__interrupt__"][0].value
    second = harness.resume({"action": "approve"})["__interrupt__"][0].value
    assert first["id"] and second["id"] and first["id"] != second["id"]
    assert second["commands"] == ["npm install left-pad"]


def test_an_answer_meant_for_an_earlier_pause_changes_nothing(make_harness, tmp_path, monkeypatch):
    # R16: "always everywhere" for the first pause arrives after the run paused again for another command.
    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    harness = make_harness({"implementer": [asks(MERGE), asks("npm install left-pad")]})
    first = harness.start()["__interrupt__"][0].value
    second = harness.resume({"action": "approve", "id": first["id"]})["__interrupt__"][0].value

    again = harness.resume({"action": "always_everywhere", "id": first["id"]})["__interrupt__"][0].value
    assert again["id"] == second["id"] and again["commands"] == ["npm install left-pad"]
    assert "error" in again
    assert not (tmp_path / "home" / "policy.toml").exists()
    assert not harness.deps.paths.policy_path.exists()
    state = harness.graph.get_state(harness.thread).values
    assert state["approved"] == [MERGE]


def test_an_answer_without_an_id_is_refused_for_a_pause_with_one(make_harness):
    harness = make_harness({"implementer": [asks(MERGE)]})
    harness.start()
    from langgraph.types import Command

    again = harness.graph.invoke(Command(resume={"action": "always_repo"}), harness.thread)["__interrupt__"][0].value
    assert "error" in again
    assert not harness.deps.paths.policy_path.exists()


def test_a_pause_from_before_ids_accepts_an_answer_without_one(make_harness, monkeypatch):
    # Runs paused before R16 have escalations with no id: their bare answers still work.
    harness, _ = paused(make_harness, MERGE)
    state = harness.graph.get_state(harness.thread).values
    old = {key: value for key, value in state["escalation"].items() if key != "id"}
    monkeypatch.setattr("phil.run.engine.interrupt", lambda payload: {"action": "approve"})
    result = harness.engine.escalate({**state, "escalation": old})
    assert result["approved"] == [MERGE]
