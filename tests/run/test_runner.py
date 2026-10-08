import pytest

from phil.config import PhilConfig
from phil.run import runner
from phil.store.telemetry import run_usage
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


def test_continue_records_the_raised_limits(make_harness):
    """Using the existing budget-escalation engine test pattern: answering a budget pause with
    continue appends exactly one budget_raised event whose max_cost_usd == run cost at that moment +
    config.run.max_cost_usd and max_tokens == tokens + config.run.max_tokens (the same values the
    state's budget_limit_* get)."""
    config = PhilConfig.model_validate({"run": {"max_tokens": 100}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config, usage=(100, 20, 0.0),
    )
    outcome_start(harness)
    totals = run_usage(harness.deps.conn, harness.deps.run_id)
    runner.resume(harness.engine, harness.graph, {"action": "continue"})
    raised = [e for e in harness.deps.events.read()[0] if e["kind"] == "budget_raised"]
    assert len(raised) == 1
    assert raised[0]["max_cost_usd"] == totals.cost_usd + config.run.max_cost_usd
    assert raised[0]["max_tokens"] == totals.tokens + config.run.max_tokens


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


def test_budget_warning_fires_once_at_eighty_percent_and_completes(make_harness):
    # warn_at defaults to 0.8; max_tokens=700 keeps every budget check under the 700-token limit
    # (checks happen before that node's own call, so the run never sees >=700 at a check), but the
    # review check (after 3 calls of 200 tokens each = 600) crosses warn_at * 700 = 560.
    config = PhilConfig.model_validate({"run": {"max_tokens": 700}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config, usage=(160, 40, 0.0),
    )
    done = outcome_start(harness)
    assert done.status == "completed"
    warnings = [e for e in harness.deps.events.read()[0] if e["kind"] == "budget_warning"]
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning["tokens"] == 600
    assert warning["max_tokens"] == 700
    assert warning["cost_usd"] == 0.0
    assert warning["max_cost_usd"] == 2.0
    assert warning["cost_source"] == "reported"


def test_budget_warning_also_fires_alongside_an_escalation_that_crosses_the_limit(make_harness):
    config = PhilConfig.model_validate({"run": {"max_tokens": 100}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config, usage=(100, 20, 0.0),
    )
    paused = outcome_start(harness)
    assert paused.escalation["reason"] == "budget"  # crossing 100% still escalates as before
    warnings = [e for e in harness.deps.events.read()[0] if e["kind"] == "budget_warning"]
    assert len(warnings) == 1
    assert warnings[0]["tokens"] == 120


def test_budget_warned_resets_when_continue_raises_the_limit(make_harness):
    config = PhilConfig.model_validate({"run": {"max_tokens": 100}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config, usage=(100, 20, 0.0),
    )
    outcome_start(harness)
    again = runner.resume(harness.engine, harness.graph, {"action": "continue"})
    assert again.escalation["reason"] == "budget"  # the raised limit is crossed again by more usage
    warnings = [e for e in harness.deps.events.read()[0] if e["kind"] == "budget_warning"]
    assert [w["tokens"] for w in warnings] == [120, 240]  # fired once per limit, not just once ever


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
