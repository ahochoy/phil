import shlex
import sys
from pathlib import Path

import phil.run.engine as engine_module
from phil.config import PhilConfig
from phil.contracts import Plan, Task
from phil.run.state import initial_state
from tests.run.conftest import TEST_CMD, review, tester_report, write_green, write_red
from tests.run.test_engine_check import write_page

PY = shlex.quote(sys.executable)
MISSING = "definitely-not-a-program-xyz"
COULDNT_RUN_TAIL = (
    ". Dependencies may be missing in the run's worktree — set [project] setup_cmd (e.g. npm ci) "
    "— or the program isn't on PATH."
)
HAPPY = {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}


def setup_config(cmd: str, **project) -> PhilConfig:
    return PhilConfig(project={"setup_cmd": cmd, **project})


def py(code: str) -> str:
    return f"{PY} -c {shlex.quote(code)}"


# The setup command writes under __pycache__/, which the calc repo ignores, as installed
# dependencies (node_modules/) would be: the task gates never see them as changed files.
WRITE_MARKER = py("import os; os.makedirs('__pycache__', exist_ok=True); open('__pycache__/marker', 'w').write('x')")


def logs_dir(harness) -> Path:
    return harness.deps.artifacts.run_dir / "logs"


def test_setup_runs_in_the_worktree_before_the_baseline(make_harness, monkeypatch):
    seen: list[bool] = []
    real_run_tests = engine_module.run_tests

    def spy(cmd, worktree, **kwargs):
        if kwargs["name"] == "baseline":
            seen.append((worktree / "__pycache__" / "marker").exists())
        return real_run_tests(cmd, worktree, **kwargs)

    monkeypatch.setattr(engine_module, "run_tests", spy)
    harness = make_harness(HAPPY, config=setup_config(WRITE_MARKER))
    final = harness.start()

    assert final["status"] == "completed"
    assert seen == [True]
    assert (logs_dir(harness) / "setup.log").exists()


def test_no_setup_command_writes_no_setup_log(make_harness):
    harness = make_harness(HAPPY)
    assert harness.start()["status"] == "completed"
    assert not (logs_dir(harness) / "setup.log").exists()


def test_failing_setup_escalates_without_running_the_baseline(make_harness):
    cmd = py("import sys; print('npm ERR! missing'); sys.exit(3)")
    harness = make_harness(HAPPY, config=setup_config(cmd))
    result = harness.start()

    escalation = result["__interrupt__"][0].value
    assert escalation["reason"] == "setup_failed"
    assert escalation["options"] == ["retry", "abort"]
    assert escalation["resume_to"] == "setup"
    assert escalation["summary"] == f"Setup command `{cmd}` failed (exit 3); see the setup log."
    assert escalation["log"] == str(logs_dir(harness) / "setup.log")
    assert "npm ERR! missing" in Path(escalation["log"]).read_text()
    assert not (logs_dir(harness) / "baseline.log").exists()
    assert harness.factory.calls == []


def test_setup_that_times_out_escalates(make_harness):
    cmd = py("import time; time.sleep(30)")
    harness = make_harness(HAPPY, config=setup_config(cmd, setup_timeout_s=1))
    escalation = harness.start()["__interrupt__"][0].value

    assert escalation["reason"] == "setup_failed"
    assert escalation["summary"] == f"Setup command `{cmd}` timed out after 1s."
    assert not (logs_dir(harness) / "baseline.log").exists()


def test_setup_sees_no_secret_env_vars(make_harness, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-planted-fake")
    cmd = py(
        "import os; os.makedirs('__pycache__', exist_ok=True); "
        "open('__pycache__/env.txt', 'w').write(chr(10).join(sorted(os.environ)))"
    )
    harness = make_harness(HAPPY, config=setup_config(cmd))
    assert harness.start()["status"] == "completed"

    seen = (harness.deps.worktree / "__pycache__" / "env.txt").read_text().splitlines()
    assert "PATH" in seen
    assert "ANTHROPIC_API_KEY" not in seen


def test_setup_that_leaves_files_git_does_not_ignore_escalates(make_harness):
    # Without the escalation the task gates would see these as the task's changes, and a reset's
    # `git clean -fd` would wipe them (tests then exit 127 after a passing baseline).
    cmd = py("import os; os.makedirs('vendor', exist_ok=True); open('vendor/dep.js', 'w').write('x')")
    harness = make_harness(HAPPY, config=setup_config(cmd))
    escalation = harness.start()["__interrupt__"][0].value

    assert escalation["reason"] == "setup_failed"
    assert escalation["options"] == ["retry", "abort"]
    assert escalation["resume_to"] == "setup"
    assert escalation["summary"] == (
        f"Setup command `{cmd}` left files git doesn't ignore (vendor/); add them to .gitignore."
    )
    assert not (logs_dir(harness) / "baseline.log").exists()
    assert harness.factory.calls == []


def test_setup_names_at_most_two_unignored_paths(make_harness):
    cmd = py("[open(name, 'w').write('x') for name in ('a.txt', 'b.txt', 'c.txt')]")
    harness = make_harness(HAPPY, config=setup_config(cmd))
    escalation = harness.start()["__interrupt__"][0].value

    assert escalation["summary"] == (
        f"Setup command `{cmd}` left files git doesn't ignore (a.txt, b.txt…); add them to .gitignore."
    )


def test_setup_whose_output_git_ignores_proceeds(make_harness):
    # WRITE_MARKER writes under __pycache__/, which the calc repo's .gitignore covers.
    harness = make_harness(HAPPY, config=setup_config(WRITE_MARKER))
    assert harness.start()["status"] == "completed"


def test_retry_after_a_failed_setup_reruns_setup_then_the_baseline(make_harness, tmp_path):
    counter = tmp_path / "setup-count"
    cmd = py(
        f"import pathlib, sys; p = pathlib.Path({str(counter)!r}); "
        "n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n)); sys.exit(3 if n == 1 else 0)"
    )
    harness = make_harness(HAPPY, config=setup_config(cmd))
    assert harness.start()["__interrupt__"][0].value["reason"] == "setup_failed"

    final = harness.resume({"action": "retry"})

    assert final["status"] == "completed"
    assert counter.read_text() == "2"
    assert (logs_dir(harness) / "baseline.log").exists()
    assert harness.run_record().needs_attention is None


def test_abort_after_a_failed_setup_finishes_as_aborted(make_harness):
    harness = make_harness(HAPPY, config=setup_config(py("import sys; sys.exit(1)")))
    harness.start()
    final = harness.resume({"action": "abort"})
    assert final["status"] == "aborted"
    assert harness.run_record().state == "aborted"


def test_baseline_command_that_cannot_run_escalates_cmd_not_found(make_harness):
    harness = make_harness(HAPPY)
    state = initial_state(harness.deps.run_id, harness.plan, harness.base_sha, MISSING)
    escalation = harness.graph.invoke(state, harness.thread)["__interrupt__"][0].value

    assert escalation["reason"] == "cmd_not_found"
    assert escalation["options"] == ["retry", "abort"]
    assert escalation["resume_to"] == "setup"
    assert escalation["summary"].startswith(f"`{MISSING}` couldn't run: ")
    assert MISSING in escalation["summary"].removeprefix(f"`{MISSING}` couldn't run: ")
    assert escalation["summary"].endswith(COULDNT_RUN_TAIL)
    assert harness.factory.calls == []


def check_plan(check_cmd: str) -> Plan:
    task = Task(
        id="CALC-001",
        description="Touch the page",
        acceptance_criteria=["the check passes"],
        files_hint=["index.html"],
        verify="check",
        check_cmd=check_cmd,
    )
    return Plan(keyword="CALC", description="Copy change", tasks=[task], test_cmd=TEST_CMD)


def test_check_command_that_cannot_run_escalates_without_using_an_attempt(make_harness):
    harness = make_harness({"implementer": [write_page]}, plan=check_plan(MISSING))
    escalation = harness.start()["__interrupt__"][0].value

    assert escalation["reason"] == "cmd_not_found"
    assert escalation["options"] == ["retry", "abort"]
    assert escalation["resume_to"] == "implement"
    assert escalation["task_id"] == "CALC-001"
    assert escalation["summary"].startswith(f"`{MISSING}` couldn't run: ")
    assert escalation["summary"].endswith(COULDNT_RUN_TAIL)
    assert harness.graph.get_state(harness.thread).values["attempts"] == 0
    # The log path is the artifact store's own (resolved), not a hand-built duplicate of its layout.
    assert escalation["log"] == str(harness.deps.artifacts.log_path("check-CALC-001-1"))


def test_retry_after_a_check_that_cannot_run_goes_back_to_implement(make_harness):
    harness = make_harness({"implementer": [write_page, write_page]}, plan=check_plan(MISSING))
    harness.start()
    result = harness.resume({"action": "retry"})

    assert result["__interrupt__"][0].value["reason"] == "cmd_not_found"
    assert harness.factory.remaining() == {"implementer": 0}
