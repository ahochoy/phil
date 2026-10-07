import shlex
import sys
from pathlib import Path

import phil.run.engine as engine_module
from phil.config import PhilConfig, load_config
from phil.contracts import Plan, Task
from phil.run.state import initial_state
from tests.helpers import run_git
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


def test_a_run_pinned_to_no_setup_ignores_a_lockfile_in_its_worktree(make_harness, calc_repo):
    # The chat pins `project.setup_cmd=""` when it showed no setup command; the worker's config,
    # reloaded with that override, must not detect `npm ci` from the worktree's lockfile.
    (calc_repo / "package.json").write_text("{}")
    (calc_repo / "package-lock.json").write_text("{}")
    run_git(calc_repo, "add", "package.json", "package-lock.json")
    run_git(calc_repo, "commit", "-m", "node lockfile")
    config = load_config(calc_repo, overrides=['project.setup_cmd=""'])
    assert config.project.setup_cmd == ""
    harness = make_harness(HAPPY, config=config)

    assert harness.start()["status"] == "completed"
    assert (harness.deps.worktree / "package-lock.json").exists()
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


def test_setup_runs_with_the_configured_bash(make_harness, monkeypatch):
    seen: list[str | None] = []
    real_run_command = engine_module.run_command

    def spy(cmd, cwd, timeout_s, env=None, *, bash=None):
        seen.append(bash)
        return real_run_command(cmd, cwd, timeout_s, env=env, bash=bash)

    monkeypatch.setattr(engine_module, "run_command", spy)
    config = PhilConfig(project={"setup_cmd": WRITE_MARKER}, shell={"bash": r"D:\Git\bin\bash.exe"})
    harness = make_harness(HAPPY, config=config)
    assert harness.start()["status"] == "completed"
    assert seen[0] == r"D:\Git\bin\bash.exe"


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
        f"Setup command `{cmd}` left files git doesn't ignore (vendor/); add them to .git/info/exclude "
        "and retry, or commit them to .gitignore and start again."
    )
    assert not (logs_dir(harness) / "baseline.log").exists()
    assert harness.factory.calls == []


def test_retry_after_excluding_setup_output_proceeds_past_setup(make_harness):
    # The worktree is the base commit, so a .gitignore edit can't reach it; the common git dir's
    # info/exclude is shared by every worktree, so excluding the path there and retrying works.
    cmd = py("import os; os.makedirs('vendor', exist_ok=True); open('vendor/dep.js', 'w').write('x')")
    harness = make_harness(HAPPY, config=setup_config(cmd))
    assert harness.start()["__interrupt__"][0].value["reason"] == "setup_failed"

    common = Path(run_git(harness.deps.worktree, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = harness.deps.worktree / common
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as f:
        f.write("\nvendor/\n")

    final = harness.resume({"action": "retry"})

    assert final["status"] == "completed"
    assert (logs_dir(harness) / "baseline.log").exists()
    assert harness.run_record().needs_attention is None


def test_setup_names_at_most_two_unignored_paths(make_harness):
    cmd = py("[open(name, 'w').write('x') for name in ('a.txt', 'b.txt', 'c.txt')]")
    harness = make_harness(HAPPY, config=setup_config(cmd))
    escalation = harness.start()["__interrupt__"][0].value

    assert escalation["summary"] == (
        f"Setup command `{cmd}` left files git doesn't ignore (a.txt, b.txt…); add them to .git/info/exclude "
        "and retry, or commit them to .gitignore and start again."
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


def test_retry_after_a_baseline_that_cannot_run_reruns_setup_then_the_baseline(make_harness, tmp_path):
    # The first setup installs nothing, so the baseline's program is missing (exit 127); the second
    # installs it, as `npm ci` would install vitest. The retry must rerun setup before the baseline.
    counter = tmp_path / "setup-count"
    runner = "__pycache__/run-tests"  # ignored by the calc repo, as node_modules/ would be
    # Embedded with repr, as bytes: on Windows TEST_CMD is a quoted path with backslashes, which
    # can't sit inside a hand-written '...' literal, and text mode would end the shebang in \r.
    script = f'#!/bin/sh\nexec {TEST_CMD} "$@"\n'.encode()
    cmd = py(
        f"import os, pathlib; p = pathlib.Path({str(counter)!r}); "
        "n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n)); "
        "os.makedirs('__pycache__', exist_ok=True); "
        f"n > 1 and pathlib.Path({runner!r}).write_bytes({script!r}); "
        f"n > 1 and os.chmod({runner!r}, 0o755)"
    )
    harness = make_harness(HAPPY, config=setup_config(cmd))
    state = initial_state(harness.deps.run_id, harness.plan, harness.base_sha, f"./{runner}")
    escalation = harness.graph.invoke(state, harness.thread)["__interrupt__"][0].value
    assert escalation["reason"] == "cmd_not_found", escalation
    assert escalation["resume_to"] == "setup"

    final = harness.resume({"action": "retry"})

    assert final["status"] == "completed", final.get("__interrupt__")
    assert counter.read_text() == "2"
    assert harness.run_record().needs_attention is None


def test_a_checkpoint_from_before_setup_resumes(make_harness):
    # The setup work added no state keys, and `route_after_setup` reads `escalation` with .get: a
    # state written before it (here, without `escalation` at all) must still go through the graph.
    harness = make_harness(HAPPY)
    state = dict(initial_state(harness.deps.run_id, harness.plan, harness.base_sha, TEST_CMD))
    del state["escalation"]
    final = harness.graph.invoke(state, harness.thread)

    assert final["status"] == "completed"
    assert not (logs_dir(harness) / "setup.log").exists()


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
