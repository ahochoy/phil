import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from phil.config import ShellConfig
from phil.store.artifacts import ArtifactStore
from phil.workspace.shell import READ_ONLY, ShellPolicy, child_env, run_command, truncate_output


@dataclass
class CommandLog:
    commands: list[str] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    # Paths the agent's file tools touched, by tool name; `invoke_agent` records into it live, so
    # it survives a rejected output (ContractViolation) or a crash.
    tool_paths: dict[str, list[str]] = field(default_factory=dict)


def make_shell_tool(
    workdir: Path,
    shell: ShellConfig,
    log: CommandLog,
    artifacts: ArtifactStore | None = None,
    log_prefix: str = "",
    extra_allow: Iterable[str] = (),
    approved: Iterable[str] = (),
) -> Callable[[str], str]:
    extra_allow, approved = tuple(extra_allow), tuple(approved)
    policy = ShellPolicy(shell.allow, extra_allow=extra_allow, approved=approved, root=workdir)
    read_only = not (shell.allow or extra_allow or approved)
    env = child_env(os.environ, shell.pass_env) | {"PYTHONDONTWRITEBYTECODE": "1"}

    def run_shell(command: str) -> str:
        """Run one allowlisted command in the task worktree.

        Returns the exit code and the command's output (long output is trimmed).
        Read-only commands (ls, cat, find, grep, git status/diff/log/show/branch/ls-files, ...) run
        without needing an allowlist entry, as long as any path they touch stays inside the worktree.
        Other commands must match the project's allowlist or this run's approved commands; others
        are denied. Shell operators such as pipes, redirects, `;` and `&&` are not allowed.
        """
        reason = policy.denial_reason(command)
        if reason == "forbidden":
            log.refused.append(command)
            detail = policy.refusal_detail(command)
            return (
                f"REFUSED: `{command}` {detail} and can never run here. "
                "Use a plain allowlisted command instead."
            )
        if reason is not None:
            log.denied.append(command)
            if read_only:
                return f"DENIED: `{command}` is not read-only. Only read-only commands run here: {', '.join(READ_ONLY)}"
            allowed = ", ".join(shell.allow)
            return f"DENIED: `{command}` is not on the allowlist. Allowed patterns: {allowed}"
        log.commands.append(command)
        result = run_command(command, workdir, shell.timeout_s, env=env, bash=shell.bash)
        full_output = result.stdout + (f"\n[stderr]\n{result.stderr}" if result.stderr else "")
        status = f"exit_code: {result.exit_code}" + (" (timed out)" if result.timed_out else "")
        lines = [status]
        if artifacts is not None:
            log_name = f"{log_prefix}-shell-{len(log.commands)}" if log_prefix else f"shell-{len(log.commands)}"
            path = artifacts.write_log(log_name, full_output)
            lines.append(f"full log: {path}")
        lines.append(truncate_output(full_output, shell.max_output_lines))
        return "\n".join(lines)

    return run_shell
