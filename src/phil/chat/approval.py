from phil.config import PhilConfig
from phil.contracts import Plan
from phil.workspace.shell import ShellPolicy

GIT_POLICY_NOTE = "Commit signing or hooks are on; a failing signature or hook will pause the run."


def effective_test_cmd(plan: Plan, config: PhilConfig) -> str | None:
    return plan.test_cmd or config.project.test_cmd


def test_cmd_problem(plan: Plan, config: PhilConfig) -> str | None:
    cmd = effective_test_cmd(plan, config)
    if not cmd:
        return "the plan has no test command and phil.toml sets no [project] test_cmd"
    if ShellPolicy([]).denial_reason(cmd) == "forbidden":
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
