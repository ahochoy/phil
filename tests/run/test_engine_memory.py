import json

from phil.contracts import SelfCheck, TesterReport
from phil.contracts.memory import MemoryAdd, MemoryUpdate
from tests.run.conftest import bad_green, write_green, write_red, review


def _noted_report() -> TesterReport:
    check = SelfCheck(
        assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[], memory_notes=["tests run with pytest"]
    )
    return TesterReport(tests_added=[], issues=[], self_check=check)


def _notices(harness) -> list[str]:
    lines = (harness.deps.artifacts.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [event["message"] for event in map(json.loads, lines) if event["kind"] == "notice"]


UPDATE = MemoryUpdate(add=[MemoryAdd(section="Commands", text="Tests: `pytest -q`")])


def test_a_completed_run_learns_and_reports_the_line(make_harness):
    harness = make_harness(
        {
            "implementer": [write_red, write_green], "tester": [_noted_report()], "reviewer": [review()],
            "memory": [UPDATE],
        }
    )
    final = harness.start()
    assert final["status"] == "completed"
    summary = (harness.deps.artifacts.run_dir / "summary.md").read_text(encoding="utf-8")
    line = "Memory: +1 (Commands: Tests: `pytest -q`) · /memory to review"
    assert f"\n\n{line}\n" in summary
    assert line in _notices(harness)
    assert "- Tests: `pytest -q` <!-- phil -->" in harness.deps.paths.memory_path.read_text(encoding="utf-8")
    assert harness.factory.remaining()["memory"] == 0


def test_a_failing_memory_agent_does_not_change_the_run(make_harness):
    harness = make_harness(
        {
            "implementer": [write_red, write_green], "tester": [_noted_report()], "reviewer": [review()],
            "memory": [RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")],
        }
    )
    final = harness.start()
    assert final["status"] == "completed"
    assert harness.run_record().state == "completed"
    assert any(message.startswith("Memory wasn't updated:") for message in _notices(harness))


def test_a_run_with_nothing_to_learn_skips_the_memory_agent(make_harness):
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [_noted_report().model_copy(update={"self_check": SelfCheck(
            assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[])})],
         "reviewer": [review()], "memory": [UPDATE]}
    )
    harness.start()
    assert harness.factory.remaining()["memory"] == 1


def test_an_aborted_run_does_not_learn(make_harness):
    def noted_bad_green(turn):
        result = bad_green(turn)
        check = result.self_check.model_copy(update={"memory_notes": ["a note"]})
        return result.model_copy(update={"self_check": check})

    harness = make_harness(
        {"implementer": [write_red, noted_bad_green, noted_bad_green, noted_bad_green], "memory": [UPDATE]}
    )
    harness.start()
    final = harness.resume({"action": "abort"})
    assert final["status"] == "aborted"
    assert harness.factory.remaining()["memory"] == 1
