import pytest

from phil.config import PhilConfig
from phil.run import runner
from tests.run.conftest import bad_green, calc_plan, review, tester_report, write_green, write_red, TEST_CMD


def outcome_start(harness):
    return runner.start(harness.engine, harness.graph, plan=harness.plan, base_sha=harness.base_sha, test_cmd=TEST_CMD)


def test_start_and_resume_across_fresh_engines(make_harness):
    first = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    paused = outcome_start(first)
    assert paused.status == "escalated"
    assert paused.escalation["reason"] == "attempts"

    second = make_harness({"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]})
    done = runner.resume(second.engine, second.graph, {"action": "retry"})
    assert done == runner.RunOutcome(status="completed")


def test_continue_after_a_crash(make_harness):
    crashing = make_harness({"implementer": [write_red, RuntimeError("worker died")]})
    with pytest.raises(RuntimeError, match="worker died"):
        outcome_start(crashing)

    recovered = make_harness({"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]})
    assert runner.continue_run(recovered.engine, recovered.graph).status == "completed"


def test_budget_limit_escalates_and_can_continue(make_harness):
    config = PhilConfig.model_validate({"run": {"max_tokens": 100}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config, usage=(100, 20, 0.0),
    )
    paused = outcome_start(harness)
    assert paused.escalation["reason"] == "budget"
    assert paused.escalation["resume_to"] == "implement"
    assert paused.escalation["summary"] == "run used 120 tokens ($0.00); limit 100 tokens / $2.00"
    harness.factory.usage = (0, 0, 0.0)
    assert runner.resume(harness.engine, harness.graph, {"action": "continue"}).status == "completed"


def test_continue_raises_the_limit_so_growing_usage_escalates_again(make_harness):
    config = PhilConfig.model_validate({"run": {"max_tokens": 100}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config, usage=(100, 20, 0.0),
    )
    outcome_start(harness)
    again = runner.resume(harness.engine, harness.graph, {"action": "continue"})
    assert again.status == "escalated"
    assert again.escalation["reason"] == "budget"
    assert again.escalation["resume_to"] == "tester"
    assert again.escalation["summary"] == "run used 240 tokens ($0.00); limit 220 tokens / $2.00"


@pytest.mark.parametrize(("node", "max_tokens"), [("tester", 240), ("review", 360)])
def test_budget_guard_runs_before_tester_and_review(make_harness, node, max_tokens):
    config = PhilConfig.model_validate({"run": {"max_tokens": max_tokens}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config, usage=(100, 20, 0.0),
    )
    paused = outcome_start(harness)
    assert paused.escalation["reason"] == "budget"
    assert paused.escalation["resume_to"] == node
    assert runner.resume(harness.engine, harness.graph, {"action": "continue"}).status == "completed"


def test_budget_abort(make_harness):
    config = PhilConfig.model_validate({"run": {"max_tokens": 100}})
    harness = make_harness({"implementer": [write_red]}, config=config, usage=(100, 20, 0.0))
    outcome_start(harness)
    assert runner.resume(harness.engine, harness.graph, {"action": "abort"}).status == "aborted"


def test_pause_is_recorded_once_by_the_runner(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    paused = outcome_start(harness)
    record = harness.run_record()
    assert (record.state, record.needs_attention) == ("escalated", paused.escalation["summary"])
    escalations = [e for e in harness.deps.events.read()[0] if e["kind"] == "escalation"]
    assert [e["escalation"]["reason"] for e in escalations] == ["attempts"]
