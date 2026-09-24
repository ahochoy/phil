from tests.helpers import run_git
from tests.run.conftest import bad_green, write_red


def test_a_baseline_collection_error_does_not_hide_green_failures(make_harness, calc_repo):
    (calc_repo / "tests" / "test_tester.py").write_text(
        "from calc import multiply\n\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n"
    )
    run_git(calc_repo, "add", "-A")
    run_git(calc_repo, "commit", "-m", "tester test for missing multiply")
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "attempts"
    assert escalation["phase"] == "green"
    assert "tests/test_sub.py::test_subtract" in escalation["problems"][0]
