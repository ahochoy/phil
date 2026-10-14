"""Commands the engine runs itself (the test command, a check command, the setup command) respect
the user's deny rules (F7): a denied one never runs, and the run reports `blocked by your rule:`."""

import shlex
import sys
from datetime import date

from phil.config import PhilConfig
from phil.permissions.store import add_rule
from phil.store.paths import global_policy_path
from tests.run.conftest import TEST_CMD, bad_green, review, tester_report, write_green, write_red
from tests.run.test_engine_check import CHECK_CMD, check_plan, write_page
from tests.run.test_engine_test_cmd import NEW_CMD, logs

PY = shlex.quote(sys.executable)
HAPPY = {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}


def deny(rule: str) -> None:
    add_rule(global_policy_path(), "deny", rule, "manual", today=date(2026, 10, 13))


def test_a_denied_setup_command_never_runs(make_harness, tmp_path):
    marker = tmp_path / "ran"
    script = tmp_path / "mark.py"
    script.write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8", newline="\n")
    cmd = f"{PY} {shlex.quote(str(script))}"
    deny(cmd)
    harness = make_harness(HAPPY, config=PhilConfig(project={"setup_cmd": cmd}))
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "setup_failed"
    assert escalation["summary"] == f"blocked by your rule: {cmd}"
    assert not marker.exists()
    assert harness.factory.calls == []


def test_a_denied_test_command_never_runs_and_stops_the_run_at_setup(make_harness):
    deny(f"{TEST_CMD} *")
    harness = make_harness(HAPPY)
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "setup_failed"
    assert escalation["summary"] == f"blocked by your rule: {TEST_CMD}"
    assert harness.factory.calls == []


def test_a_denied_check_command_escalates_at_once(make_harness):
    deny("grep -q easter *")
    harness = make_harness({"implementer": [write_page]}, plan=check_plan())
    harness.start()
    escalation = harness.graph.get_state(harness.thread).values["escalation"]
    assert escalation["reason"] == "attempts"
    assert escalation["summary"] == (f"CALC-001 is blocked: your rule denies the check command {CHECK_CMD} — "
                                     "change the rule or the check command, then retry")
    assert escalation["problems"][0] == f"blocked by your rule: {CHECK_CMD}"
    assert harness.factory.remaining() == {"implementer": 0}  # one call: no retry the agent can't win
    assert not any(name.startswith("check-") for name in logs(harness))  # it never ran


def test_a_test_command_switched_mid_run_to_a_denied_one_escalates_at_once(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green, write_green]})
    harness.start()
    assert harness.graph.get_state(harness.thread).values["escalation"]["reason"] == "attempts"
    deny(f"{NEW_CMD} *")
    harness.graph.update_state(harness.thread, {"test_cmd": NEW_CMD, "rebaseline": True})
    harness.resume({"action": "retry"})
    escalation = harness.graph.get_state(harness.thread).values["escalation"]
    assert escalation["reason"] == "attempts"
    assert escalation["summary"] == (f"CALC-001 is blocked: your rule denies the test command {NEW_CMD} — "
                                     "change the rule or the test command, then retry")
    assert escalation["problems"][0] == f"blocked by your rule: {NEW_CMD}"
    assert harness.factory.remaining() == {"implementer": 0}  # the retry spent one call, not three
    rebaselines = [name for name in logs(harness) if name.startswith("rebaseline")]
    assert rebaselines  # the switch rebaselined, without running the denied command:
    for name in rebaselines:
        text = (harness.deps.artifacts.run_dir / "logs" / name).read_text(encoding="utf-8")
        assert text == f"blocked by your rule: {NEW_CMD}\n"
