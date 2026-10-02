import shlex
import shutil
from pathlib import Path

from phil.config import RUN_ROLES, PhilConfig
from phil.contracts import Plan
from phil.repo_detect import detect_test_cmd
from phil.repo_detect import effective_setup_cmd as effective_setup_cmd  # the engine shares it
from phil.workspace.shell import CONTAINMENT_DETAIL, ShellPolicy

GIT_POLICY_NOTE = "Commit signing or hooks are on; a failing signature or hook will pause the run."


def terminated(text: str) -> str:
    """`text` with a trailing period, without doubling one it already ends with."""
    return text if text.endswith(".") else f"{text}."


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
        return "the plan has no test command and your config sets no [project] test_cmd"
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


def setup_cmd_problem(config: PhilConfig, root: Path | None = None) -> str | None:
    """None unless the effective setup command uses shell operators or a blocked command: setup
    runs without a shell, so e.g. `npm ci && npm run build` would fail confusingly."""
    cmd, _ = effective_setup_cmd(config, root)
    if not cmd:
        return None
    if ShellPolicy(config.shell.allow).denial_reason(cmd) == "forbidden":
        return f"setup command `{cmd}` uses shell operators or a blocked command; Phil runs it directly"
    return None


test_cmd_problem.__test__ = False  # not a pytest test
test_cmd_differs.__test__ = False
setup_cmd_problem.__test__ = False


def check_cmd_problems(plan: Plan, config: PhilConfig, root: Path | None = None) -> list[str]:
    """Forbidden check commands. Anything merely off [shell] allow is fine: the run allows each check_cmd.

    `root`, when given, is the tree the paths of a read-only check command must stay inside — the
    run's worktree is a checkout of it, and the run refuses an out-of-tree one anyway."""
    policy = ShellPolicy(config.shell.allow, root=root)
    cmds = dict.fromkeys(task.check_cmd for task in plan.tasks if task.check_cmd)
    problems = []
    for cmd in cmds:
        detail = policy.refusal_detail(cmd)
        if detail is None:
            continue
        if detail == CONTAINMENT_DETAIL:
            problems.append(f"check command {cmd!r} reads outside the repo; check commands must stay inside the worktree")
        else:
            problems.append(f"check command {cmd!r} uses shell operators or a blocked command")
    return problems


def _program_problem(cmd: str, kind: str) -> str | None:
    """None if `cmd`'s program is runnable, else the exact "isn't on PATH" message.

    A path containing "/" is skipped (the containment checks cover it); a command `shlex.split`
    can't parse is skipped (the shell-policy checks cover it)."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    if not parts:
        return None
    prog = parts[0]
    if "/" in prog or shutil.which(prog):
        return None
    return f"`{prog}` (from the {kind} command `{cmd}`) isn't on PATH for Phil's runs"


def program_problems(plan: Plan, config: PhilConfig, root: Path | None = None) -> list[str]:
    """Programs of the effective setup, test and check commands that aren't on PATH.

    `root`, when given, is where a missing setup or test command is detected from (the same tree
    for both)."""
    problems = []
    setup_cmd, _ = effective_setup_cmd(config, root)
    if setup_cmd:
        problem = _program_problem(setup_cmd, "setup")
        if problem:
            problems.append(problem)
    test_cmd, _ = effective_test_cmd(plan, config, root)
    if test_cmd:
        problem = _program_problem(test_cmd, "test")
        if problem:
            problems.append(problem)
    check_cmds = dict.fromkeys(task.check_cmd for task in plan.tasks if task.check_cmd)
    for cmd in check_cmds:
        problem = _program_problem(cmd, "check")
        if problem:
            problems.append(problem)
    return list(dict.fromkeys(problems))


def git_policy_note(config: PhilConfig) -> str | None:
    if config.git.sign_commits is not False or config.git.run_hooks:
        return GIT_POLICY_NOTE
    return None


def launch_problems(
    plan: Plan, config: PhilConfig, root: Path | None = None, *, check_root: Path | None = None
) -> list[str]:
    """Why a run of `plan` can't start under `config` (empty when it can). Callers escape before printing,
    and should use `terminated()` rather than assuming a problem needs a trailing period added.

    `root`, when given, is where a missing setup or test command is detected from. `check_root`
    (default: `root`) is the tree check commands' paths must stay inside."""
    problems = list(config.missing_model_messages(RUN_ROLES))
    problem = test_cmd_problem(plan, config, root)
    if problem:
        problems.append(problem)
    setup_problem = setup_cmd_problem(config, root)
    if setup_problem:
        problems.append(setup_problem)
    problems += check_cmd_problems(plan, config, check_root if check_root is not None else root)
    problems += program_problems(plan, config, root)
    return problems
