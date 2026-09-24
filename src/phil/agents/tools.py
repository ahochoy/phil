import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from phil.config import ShellConfig
from phil.store.artifacts import ArtifactStore
from phil.workspace.shell import ShellPolicy, child_env, run_command, truncate_output


@dataclass
class CommandLog:
    commands: list[str] = field(default_factory=list)


def make_shell_tool(
    workdir: Path,
    shell: ShellConfig,
    log: CommandLog,
    artifacts: ArtifactStore | None = None,
    log_prefix: str = "",
) -> Callable[[str], str]:
    policy = ShellPolicy(shell.allow)
    env = child_env(os.environ, shell.pass_env)

    def run_shell(command: str) -> str:
        """Run one allowlisted command in the task worktree.

        Returns the exit code and the command's output (long output is trimmed).
        Only commands matching the project's allowlist run; others are denied.
        Shell operators such as pipes, redirects, `;` and `&&` are not allowed.
        """
        if not policy.is_allowed(command):
            allowed = ", ".join(shell.allow)
            return f"DENIED: `{command}` is not on the allowlist. Allowed patterns: {allowed}"
        log.commands.append(command)
        result = run_command(command, workdir, shell.timeout_s, env=env)
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
