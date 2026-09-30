from pathlib import Path

from phil.config import RUN_ROLES, PhilConfig
from phil.contracts import Plan
from phil.repo_detect import detect_test_cmd
from phil.workspace.shell import ShellPolicy

GIT_POLICY_NOTE = "Commit signing or hooks are on; a failing signature or hook will pause the run."


def effective_test_cmd(plan: Plan, config: PhilConfig, root: Path | None = None) -> tuple[str | None, str]:
    """The test command a run of `plan` uses and where it came from: "plan", "config", "detected" or "none".

    Detection looks at `root` (the repo snapshot the run will see) only when neither the plan nor
    phil.toml sets a command."""
    if plan.test_cmd:
        return plan.test_cmd, "plan"
    if config.project.test_cmd:
        return config.project.test_cmd, "config"
    detected = detect_test_cmd(root) if root is not None else None
    if detected:
        return detected, "detected"
    return None, "none"


def test_cmd_problem(plan: Plan, config: PhilConfig, root: Path | None = None) -> str | None:
    cmd, _ = effective_test_cmd(plan, config, root)
    if not cmd:
        if plan.tasks and all(task.verify == "check" for task in plan.tasks):
            return None  # every task is verified by its check_cmd: the run needs no test suite
        return "the plan has no test command and phil.toml sets no [project] test_cmd"
    if cmd == config.project.test_cmd:
        return None
    # Approving the plan approves its own test command (or the detected one it shows), so only a
    # genuinely forbidden one (shell operators, a blocked flag) is rejected here; anything merely
    # off [shell] allow is fine — the run allows it for the plan's test command regardless.
    if ShellPolicy(config.shell.allow).denial_reason(cmd) == "forbidden":
        return f"test command {cmd!r} uses shell operators or a blocked command; Phil runs it directly"
    return None


def test_cmd_differs(plan: Plan, config: PhilConfig) -> bool:
    return bool(plan.test_cmd and config.project.test_cmd and plan.test_cmd != config.project.test_cmd)


test_cmd_problem.__test__ = False  # not a pytest test
test_cmd_differs.__test__ = False


def check_cmd_problems(plan: Plan, config: PhilConfig) -> list[str]:
    """Forbidden check commands. Anything merely off [shell] allow is fine: the run allows each check_cmd."""
    policy = ShellPolicy(config.shell.allow)
    cmds = dict.fromkeys(task.check_cmd for task in plan.tasks if task.check_cmd)
    return [
        f"check command {cmd!r} uses shell operators or a blocked command"
        for cmd in cmds
        if policy.denial_reason(cmd) == "forbidden"
    ]


def git_policy_note(config: PhilConfig) -> str | None:
    if config.git.sign_commits is not False or config.git.run_hooks:
        return GIT_POLICY_NOTE
    return None


def launch_problems(plan: Plan, config: PhilConfig, root: Path | None = None) -> list[str]:
    """Why a run of `plan` can't start under `config` (empty when it can). Callers escape before printing.

    `root`, when given, is where a missing test command is detected from."""
    problems = []
    missing = config.missing_models(RUN_ROLES)
    if missing:
        problems.append(f"phil.toml sets no model for: {', '.join(missing)}. Add them under [models]")
    problem = test_cmd_problem(plan, config, root)
    if problem:
        problems.append(problem)
    problems += check_cmd_problems(plan, config)
    return problems
