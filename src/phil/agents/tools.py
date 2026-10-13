import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from phil.config import ShellConfig
from phil.permissions.store import PolicySet, config_policy
from phil.store.artifacts import ArtifactStore
from phil.workspace.shell import READ_ONLY, ShellPolicy, child_env, run_command, truncate_output


@dataclass
class CommandLog:
    commands: list[str] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)  # stopped by a deny rule: reported, never asked about
    # Each denied command's label (`changes things`...), as the shell policy decided it.
    labels: dict[str, str] = field(default_factory=dict)
    # Paths the agent's file tools touched, by tool name; `invoke_agent` records into it live, so
    # it survives a rejected output (ContractViolation) or a crash.
    tool_paths: dict[str, list[str]] = field(default_factory=dict)


MAX_LISTED_PATTERNS = 10


def _pattern_list(patterns: list[str]) -> str:
    """The allowed patterns an ask names, at most MAX_LISTED_PATTERNS of them."""
    listed = ", ".join(patterns[:MAX_LISTED_PATTERNS])
    more = len(patterns) - MAX_LISTED_PATTERNS
    return f"{listed} … ({more} more: phil permissions)" if more > 0 else listed


def make_shell_tool(
    workdir: Path,
    shell: ShellConfig,
    log: CommandLog,
    artifacts: ArtifactStore | None = None,
    log_prefix: str = "",
    extra_allow: Iterable[str] = (),
    approved: Iterable[str] = (),
    *,
    policy: PolicySet | None = None,
    read_only: bool = False,
) -> Callable[[str], str]:
    """`policy` is the permission rules in effect (`permissions.load.policy_for`); None means
    `shell.allow` alone, as config entries. No deny rules: production callers must pass a policy
    (see policy_for). `read_only` is for the read-only agents: only the forbidden checks and the
    built-in reads apply."""
    extra_allow, approved = tuple(extra_allow), tuple(approved)
    rules = policy if policy is not None else config_policy(list(shell.allow))
    shell_policy = ShellPolicy(rules, extra_allow=extra_allow, approved=approved, root=workdir, read_only=read_only)
    reads_only = read_only or not (rules.allow or extra_allow or approved)
    allowed_patterns = _pattern_list([entry.rule.text for entry in rules.allow])
    env = child_env(os.environ, shell.pass_env) | {"PYTHONDONTWRITEBYTECODE": "1"}

    def run_shell(command: str) -> str:
        """Run one allowlisted command in the task worktree.

        Returns the exit code and the command's output (long output is trimmed).
        Read-only commands (ls, cat, find, grep, git status/diff/log/show/branch/ls-files, ...) run
        without needing an allowlist entry, as long as any path they touch stays inside the worktree.
        Other commands must match the project's allowlist or this run's approved commands; others
        are denied. Shell operators such as pipes, redirects, `;` and `&&` are not allowed.
        """
        decision = shell_policy.decide(command)
        if decision.outcome == "forbidden":
            log.refused.append(command)
            return (
                f"REFUSED: `{command}` {decision.detail} and can never run here. "
                "Use a plain allowlisted command instead."
            )
        if decision.outcome == "deny":
            # A deny rule is the user's standing answer: it never becomes an approval prompt.
            log.blocked.append(command)
            return f"DENIED: `{command}` is blocked by your {decision.reason}. Don't use it."
        if decision.outcome == "ask":
            log.denied.append(command)
            log.labels[command] = decision.label
            if reads_only:
                return f"DENIED: `{command}` is not read-only. Only read-only commands run here: {', '.join(READ_ONLY)}"
            return f"DENIED: `{command}` is not on the allowlist. Allowed patterns: {allowed_patterns}"
        log.commands.append(command)
        result = run_command(command, workdir, shell.timeout_s, env=env, bash=shell.bash)
        full_output = result.stdout + (f"\n[stderr]\n{result.stderr}" if result.stderr else "")
        status = f"exit_code: {result.exit_code}" + (" (timed out)" if result.timed_out else "")
        lines = [status, f"allowed: {decision.reason}"]
        if artifacts is not None:
            log_name = f"{log_prefix}-shell-{len(log.commands)}" if log_prefix else f"shell-{len(log.commands)}"
            path = artifacts.write_log(log_name, full_output)
            lines.append(f"full log: {path}")
        lines.append(truncate_output(full_output, shell.max_output_lines))
        return "\n".join(lines)

    return run_shell
