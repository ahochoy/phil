from phil.config import PhilConfig
from phil.run.state import load_plan
from tests.helpers import run_git
from tests.run.conftest import review, tester_report, write_green, write_red

FAILING_HOOK = '#!/bin/sh\necho "hook says no" >&2\nexit 1\n'


def _install_failing_hook(calc_repo):
    hook = calc_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(FAILING_HOOK)
    hook.chmod(0o755)


def test_failing_hook_escalates_as_commit_failed(make_harness, calc_repo):
    _install_failing_hook(calc_repo)
    config = PhilConfig.model_validate({"git": {"run_hooks": True}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config,
    )
    result = harness.start()
    escalation = result["__interrupt__"][0].value
    assert escalation["reason"] == "commit_failed"
    assert escalation["options"] == ["retry", "bypass", "abort"]
    assert "hook says no" in escalation["problems"][0]
    assert load_plan(result).tasks[0].status != "DONE"


def test_bypass_completes_the_run_and_commits(make_harness, calc_repo):
    _install_failing_hook(calc_repo)
    config = PhilConfig.model_validate({"git": {"run_hooks": True}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config,
    )
    harness.start()
    final = harness.resume({"action": "bypass"})
    assert final["status"] == "completed"
    log = run_git(calc_repo, "log", "--format=%s", "phil/r-0001")
    assert log.splitlines()[0] == "CALC-001: Add subtract"


def test_retry_after_removing_hook_completes(make_harness, calc_repo):
    _install_failing_hook(calc_repo)
    config = PhilConfig.model_validate({"git": {"run_hooks": True}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config,
    )
    harness.start()
    (calc_repo / ".git" / "hooks" / "pre-commit").unlink()
    final = harness.resume({"action": "retry"})
    assert final["status"] == "completed"


def test_abort_finishes_aborted(make_harness, calc_repo):
    _install_failing_hook(calc_repo)
    config = PhilConfig.model_validate({"git": {"run_hooks": True}})
    harness = make_harness({"implementer": [write_red, write_green]}, config=config)
    harness.start()
    final = harness.resume({"action": "abort"})
    assert final["status"] == "aborted"


def test_default_config_skips_hooks_so_run_completes(make_harness, calc_repo):
    _install_failing_hook(calc_repo)
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert final["status"] == "completed"
