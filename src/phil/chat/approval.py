from phil.config import RUN_ROLES, PhilConfig
from phil.contracts import Plan
from phil.workspace.shell import ShellPolicy

GIT_POLICY_NOTE = "Commit signing or hooks are on; a failing signature or hook will pause the run."


def effective_test_cmd(plan: Plan, config: PhilConfig) -> str | None:
    return plan.test_cmd or config.project.test_cmd


def test_cmd_problem(plan: Plan, config: PhilConfig) -> str | None:
    cmd = effective_test_cmd(plan, config)
    if not cmd:
        return "the plan has no test command and phil.toml sets no [project] test_cmd"
    if cmd == config.project.test_cmd:
        return None
    # Approving the plan approves its own test command, so only a genuinely forbidden one (shell
    # operators, a blocked flag) is rejected here; anything merely off [shell] allow is fine — the
    # run allows it for the plan's test command regardless.
    if ShellPolicy(config.shell.allow).denial_reason(cmd) == "forbidden":
        return f"test command {cmd!r} uses shell operators or a blocked command; Phil runs it directly"
    return None


def test_cmd_differs(plan: Plan, config: PhilConfig) -> bool:
    return bool(plan.test_cmd and config.project.test_cmd and plan.test_cmd != config.project.test_cmd)


test_cmd_problem.__test__ = False  # not a pytest test
test_cmd_differs.__test__ = False


def git_policy_note(config: PhilConfig) -> str | None:
    if config.git.sign_commits is not False or config.git.run_hooks:
        return GIT_POLICY_NOTE
    return None


def launch_problems(plan: Plan, config: PhilConfig) -> list[str]:
    """Why a run of `plan` can't start under `config` (empty when it can). Callers escape before printing."""
    problems = []
    missing = config.missing_models(RUN_ROLES)
    if missing:
        problems.append(f"phil.toml sets no model for: {', '.join(missing)}. Add them under [models]")
    problem = test_cmd_problem(plan, config)
    if problem:
        problems.append(problem)
    return problems
