import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import ClassVar

import pytest

from phil import platform
from phil.config import ShellConfig
from phil.workspace import shell as shell_module
from phil.workspace.shell import (
    READ_ONLY,
    ShellPolicy,
    child_env,
    is_secret_name,
    literal_pattern,
    normalise_path_text,
    run_command,
    truncate_output,
)

PY = shlex.quote(sys.executable)


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("pytest -q", True),
        ("git status", True),
        ("git push", False),
        ("pytest; rm -rf /", False),
        ("pytest && echo hi", False),
        ("pytest | tee out", False),
        ("echo $(whoami)", False),
        ("pytest > out.txt", False),
        ("pytestevil", False),
    ],
)
def test_policy(command, allowed):
    assert ShellPolicy(["pytest *", "pytest", "git status"]).is_allowed(command) is allowed


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("uv run bash -c 'rm -rf /'", False),
        ("git diff --output=/tmp/x", False),
        ("git diff --no-index a b", False),
        ("uv run pytest -q", True),
        ("git diff HEAD~1", True),
    ],
)
def test_policy_default_allowlist(command, allowed):
    assert ShellPolicy(ShellConfig().allow).is_allowed(command) is allowed


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("pytest -q", None),
        ("git push", "not_allowed"),
        ("pytest && echo hi", "forbidden"),
        ("python -c 'print(1)'", "forbidden"),
        ("pytest 'unterminated", "forbidden"),
    ],
)
def test_denial_reason(command, reason):
    policy = ShellPolicy(["pytest *", "pytest", "git status", "python *"])
    assert policy.denial_reason(command) == reason
    assert policy.is_allowed(command) is (reason is None)


def test_run_command_captures_output(tmp_path):
    result = run_command(f"{PY} -c \"print('hi')\"", cwd=tmp_path, timeout_s=10)
    assert result.ok
    assert result.stdout.strip() == "hi"
    assert result.exit_code == 0


def test_run_command_nonzero_exit(tmp_path):
    result = run_command(f'{PY} -c "import sys; sys.exit(3)"', cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 3
    assert not result.ok


def test_run_command_missing_program(tmp_path):
    result = run_command("definitely-not-a-real-program-xyz", cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 127
    assert not result.ok


def test_run_command_times_out(tmp_path):
    result = run_command(f'{PY} -c "import time; time.sleep(10)"', cwd=tmp_path, timeout_s=0.5)
    assert result.timed_out
    assert not result.ok
    assert result.duration_ms < 5000


def test_truncate_short_text_unchanged():
    assert truncate_output("a\nb\nc", max_lines=10) == "a\nb\nc"


def test_truncate_keeps_head_tail_and_failures():
    lines = [f"line {i}" for i in range(1000)]
    lines[500] = "FAILED tests/test_map.py::test_markers"
    out = truncate_output("\n".join(lines), max_lines=40).splitlines()
    assert out[0] == "line 0"
    assert out[-1] == "line 999"
    assert "FAILED tests/test_map.py::test_markers" in out
    assert any("lines omitted" in line for line in out)
    assert len(out) <= 41


def test_run_command_malformed_quoting(tmp_path):
    result = run_command('pytest "unterminated', cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 2
    assert not result.ok
    assert "quotation" in result.stderr


def test_run_command_replaces_invalid_utf8(tmp_path):
    result = run_command(
        f"{PY} -c \"import sys; sys.stdout.buffer.write(b'\\xff\\xfe')\"",
        cwd=tmp_path,
        timeout_s=10,
    )
    assert result.ok
    assert result.exit_code == 0
    assert "�" in result.stdout


def test_run_command_missing_cwd(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = run_command(f"{PY} -c \"print('hi')\"", cwd=missing, timeout_s=10)
    assert result.exit_code == 2
    assert not result.ok
    assert "working directory does not exist" in result.stderr
    assert str(missing) in result.stderr


def test_run_command_non_executable_file(tmp_path):
    script = tmp_path / "not-executable.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o644)
    result = run_command(str(script), cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 126
    assert not result.ok


@pytest.mark.parametrize(
    "name",
    ["OPENROUTER_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD", "MY_APIKEY", "NPM_AUTH", "gh_token"],
)
def test_secret_names_are_detected(name):
    assert is_secret_name(name)


@pytest.mark.parametrize("name", ["PATH", "HOME", "GIT_AUTHOR_NAME", "KEYBOARD_LAYOUT", "LANG", "VIRTUAL_ENV"])
def test_ordinary_names_are_kept(name):
    assert not is_secret_name(name)


NULL_KEYRING = {"PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring"}


def test_child_env_strips_secrets_but_honours_pass_env():
    environ = {"PATH": "/bin", "OPENROUTER_API_KEY": "sk-1", "DATABASE_TOKEN": "t"}
    assert child_env(environ) == {"PATH": "/bin"} | NULL_KEYRING
    assert child_env(environ, pass_env=["DATABASE_TOKEN"]) == {"PATH": "/bin", "DATABASE_TOKEN": "t"} | NULL_KEYRING


def test_child_env_pins_the_null_keyring_even_over_the_parent_or_pass_env():
    environ = {"PATH": "/bin", "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    assert child_env(environ, pass_env=["PYTHON_KEYRING_BACKEND"]) == {"PATH": "/bin"} | NULL_KEYRING


def test_a_child_command_sees_only_the_null_keyring(tmp_path, monkeypatch):
    # The parent pins a different backend, so only child_env can make the child report the null one.
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    script = tmp_path / "show_backend.py"
    script.write_text("import keyring\nprint(type(keyring.get_keyring()).__module__)\n")
    result = run_command(
        f"{PY} {shlex.quote(str(script))}", cwd=tmp_path, timeout_s=30, env=child_env(os.environ)
    )
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "keyring.backends.null"


def test_run_command_hides_secrets_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "sk-should-not-leak")
    script = tmp_path / "show_env.py"
    script.write_text("import os\nprint(os.environ.get('FAKE_API_KEY', 'absent'))\n")
    result = run_command(f"{PY} {shlex.quote(str(script))}", cwd=tmp_path, timeout_s=10)
    assert result.stdout.strip() == "absent"


def test_run_command_uses_explicit_env(tmp_path):
    script = tmp_path / "show_env.py"
    script.write_text("import os\nprint(os.environ.get('ONLY_THIS', 'absent'))\n")
    result = run_command(f"{PY} {shlex.quote(str(script))}", cwd=tmp_path, timeout_s=10, env={"ONLY_THIS": "yes"})
    assert result.stdout.strip() == "yes"


@pytest.mark.parametrize("command", READ_ONLY)
def test_read_only_commands_are_allowed_with_an_empty_allow_list(command):
    assert ShellPolicy([]).is_allowed(command)


def test_find_delete_and_exec_are_forbidden():
    policy = ShellPolicy([])
    assert policy.is_allowed("find . -name x")
    assert policy.denial_reason("find . -delete") == "forbidden"
    assert policy.denial_reason("find . -exec rm {} ;") == "forbidden"
    assert policy.denial_reason("find . -exec rm {} +") == "forbidden"


@pytest.mark.parametrize(
    "command", ["find . -fprint out.txt", "find . -fprint0 out.txt", "find . -fprintf fmt out.txt"]
)
def test_find_fprint_family_is_forbidden(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


@pytest.mark.parametrize("command", ["find -L . -name x", "find . -follow"])
def test_find_symlink_following_flags_are_forbidden(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


@pytest.mark.parametrize(
    "command",
    [
        "git branch -qD x",
        "git branch -rd origin/x",
        "git branch --del x",
        "git branch --mo a b",
        "git branch newb",
        "git branch -D x",
        "git branch --set-upstream-to=origin/x",
        "git branch --unset-upstream",
        "git branch --track",
        "git branch --edit-description",
    ],
)
def test_git_branch_refuses_anything_but_listing(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


@pytest.mark.parametrize(
    "command",
    [
        "git branch",
        "git branch -a",
        "git branch --list 'feat*'",
        "git branch --show-current",
        "git branch --sort=-committerdate",
        "git branch --format=%(refname)",
    ],
)
def test_git_branch_allows_listing(command):
    assert ShellPolicy([]).is_allowed(command)


def test_git_log_oneline_is_allowed():
    assert ShellPolicy([]).is_allowed("git log --oneline")


@pytest.mark.parametrize(
    "command", ["rg --pre sh x .", "rg --pre=sh x .", "rg --pre-glob '*.sh' x .", "rg --pre-glob=*.sh x ."]
)
def test_rg_pre_hook_flags_are_forbidden(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


def test_rg_plain_search_is_allowed():
    assert ShellPolicy([]).is_allowed("rg x .")


@pytest.mark.parametrize(
    "command", ["rg --hostname-bin=/bin/sh x .", "rg --hostname-bin sh x ."]
)
def test_rg_hostname_bin_is_forbidden(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


@pytest.mark.parametrize("command", ["grep -R x .", "grep -Rn x .", "grep --dereference-recursive x ."])
def test_grep_recursive_dereference_is_forbidden(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


def test_grep_lowercase_recursive_is_allowed():
    assert ShellPolicy([]).is_allowed("grep -r x .")


@pytest.mark.parametrize(
    "command",
    [
        "grep -S x .",
        "grep -rS x .",
        "grep -rnS x .",
        "grep -O x .",
        "grep -rO x .",
        "grep -p x .",
        "grep -rp x .",
    ],
)
def test_grep_bsd_symlink_flags_are_forbidden(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


def test_grep_pattern_attached_to_dash_e_is_not_mistaken_for_more_flags():
    assert ShellPolicy([]).is_allowed("grep -eR src")


def test_rg_pattern_attached_to_dash_e_is_not_mistaken_for_more_flags():
    assert ShellPolicy([]).is_allowed("rg -eL")


@pytest.mark.parametrize("command", ["rg -L x .", "rg -Ln x .", "rg --follow x ."])
def test_rg_follow_symlinks_is_forbidden(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


@pytest.mark.parametrize("command", ["ls -L", "ls -lL", "ls --dereference"])
def test_ls_dereference_is_forbidden(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


def test_ls_plain_long_listing_is_allowed():
    assert ShellPolicy([]).is_allowed("ls -la")


def test_containment_blocks_paths_outside_root(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.ts").write_text("")
    policy = ShellPolicy([], root=tmp_path)
    assert policy.denial_reason("cat /etc/passwd") == "forbidden"
    assert policy.denial_reason("ls ../..") == "forbidden"
    assert policy.is_allowed("cat src/x.ts")
    assert policy.is_allowed("ls -la")


@pytest.mark.parametrize(
    "command",
    [
        "grep --file=/etc/hosts x .",
        "grep -f/etc/hosts x .",
        "wc --files0-from=/etc/hosts",
    ],
)
def test_containment_catches_attached_flag_path_values(tmp_path, command):
    assert ShellPolicy([], root=tmp_path).denial_reason(command) == "forbidden"


def test_containment_still_catches_a_separate_flag_argument(tmp_path):
    assert ShellPolicy([], root=tmp_path).denial_reason("grep -f /etc/hosts x .") == "forbidden"


def test_containment_allows_harmless_flag_values(tmp_path):
    policy = ShellPolicy([], root=tmp_path)
    assert policy.is_allowed("git branch --sort=-committerdate")
    assert policy.is_allowed("git branch --format=%(refname)")


def test_grep_rg_positional_pattern_is_excluded_from_path_containment(tmp_path):
    policy = ShellPolicy([], root=tmp_path)
    assert policy.is_allowed("grep -rn /api/ src")
    assert policy.is_allowed("rg /etc/ src")


def test_grep_rg_second_positional_is_still_a_real_path(tmp_path):
    policy = ShellPolicy([], root=tmp_path)
    assert policy.denial_reason("grep -rn foo /etc") == "forbidden"


def test_grep_with_explicit_pattern_flag_treats_all_positionals_as_paths(tmp_path):
    assert ShellPolicy([], root=tmp_path).denial_reason("grep -e foo /etc") == "forbidden"


@pytest.fixture
def worktree_with_outside_file(tmp_path):
    root = tmp_path / "wt"
    (root / "src").mkdir(parents=True)
    (root / ".ignore").write_text("*.log\n")
    (tmp_path / "outside").write_text("secret\n")
    return root


@pytest.mark.parametrize(
    "command",
    [
        "rg --ignore-file ../outside foo .",
        "rg --file ../outside .",
        "grep --exclude-from ../outside foo .",
        "grep --file ../outside .",
        # A separate value for a non-file option shifts the pattern to the next positional.
        "grep -A 3 foo ../outside",
        "grep -A 3 foo /etc",
        "rg -g '*.py' foo ../outside",
        "rg --max-depth 2 foo ../outside",
        "rg -d 2 foo ../outside",
        # After `--`, the first token is the pattern even if it looks like a flag.
        "grep -- -x ../outside",
        # GNU/BSD grep accept abbreviated long options; an abbreviation is never trusted.
        "grep --exclude-f ../outside foo .",
        "grep --regex foo ../outside",
        # BSD grep's --context takes its value only via `=`, GNU's also as a separate token.
        "grep --context 3 ../outside .",
        # `rg --files` takes no pattern: every positional is a path to list.
        "rg --files ../outside",
    ],
)
def test_grep_rg_separate_option_values_are_never_taken_as_the_pattern(
    worktree_with_outside_file, command
):
    policy = ShellPolicy([], root=worktree_with_outside_file)
    assert policy.denial_reason(command) == "forbidden"


@pytest.mark.parametrize(
    "command",
    [
        "rg --ignore-file .ignore foo .",
        "grep -A 3 foo src",
        "grep -A 3 /api/ src",
        "grep --max-count 2 /api/ src",
        "rg -g '*.py' /api/ src",
        "rg --type py /api/ src",
        "grep -rn -- /api/ src",
    ],
)
def test_grep_rg_pattern_after_consumed_option_values_is_still_excluded(
    worktree_with_outside_file, command
):
    assert ShellPolicy([], root=worktree_with_outside_file).is_allowed(command)


def test_symlink_escaping_the_worktree_is_forbidden(tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret")
    link = tmp_path / "link"
    link.symlink_to(outside)
    assert ShellPolicy([], root=tmp_path).denial_reason("cat link") == "forbidden"


def test_symlink_inside_the_worktree_is_allowed(tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("hi")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert ShellPolicy([], root=tmp_path).is_allowed("cat link")


def test_symlink_named_like_a_flag_after_double_dash_is_caught(tmp_path):
    outside = tmp_path.parent / "outside-secret2.txt"
    outside.write_text("secret")
    link = tmp_path / "-evil"
    link.symlink_to(outside)
    assert ShellPolicy([], root=tmp_path).denial_reason("cat -- -evil") == "forbidden"


def test_symlink_named_by_an_attached_short_flag_value_is_caught(tmp_path):
    outside = tmp_path.parent / "outside-secret3.txt"
    outside.write_text("secret")
    link = tmp_path / "evil"
    link.symlink_to(outside)
    assert ShellPolicy([], root=tmp_path).denial_reason("grep -fevil x .") == "forbidden"


def test_symlink_loop_does_not_crash_containment_checks(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.symlink_to(b)
    b.symlink_to(a)
    # Neither raises nor hangs; a loop that never escapes root resolves to something still
    # (nominally) inside it, so this is allowed rather than forbidden.
    assert ShellPolicy([], root=tmp_path).is_allowed("cat a")


def test_extra_allow_matches_literal_tokens_with_trailing_arguments():
    assert ShellPolicy([]).denial_reason("npm run build") == "not_allowed"
    policy = ShellPolicy([], extra_allow=("npm run build",))
    assert policy.is_allowed("npm run build")
    assert policy.is_allowed("npm run build --watch")


def test_extra_allow_escapes_glob_characters_in_the_command():
    cmd = "pytest tests/test_foo.py::test_bar[case1]"
    policy = ShellPolicy([], extra_allow=(cmd,))
    assert policy.is_allowed(cmd)
    assert policy.is_allowed(cmd + " -v")
    assert not policy.is_allowed("pytest tests/test_foo.py::test_bar[XYZ9]")


def test_approved_command_matches_only_exactly():
    policy = ShellPolicy([], approved=("rm build.log",))
    assert policy.is_allowed("rm build.log")
    assert not policy.is_allowed("rm build.log -rf /Users/x")


def test_approved_command_with_brackets_matches_only_itself():
    cmd = "pytest tests/test_foo.py::test_bar[case1]"
    policy = ShellPolicy([], approved=(cmd,))
    assert policy.is_allowed(cmd)
    assert not policy.is_allowed("pytest tests/test_foo.py::test_bar[XYZ9]")
    assert not policy.is_allowed("pytest tests/test_foo.py::test_bar1")


def test_grep_piped_to_head_is_still_forbidden():
    assert ShellPolicy([]).denial_reason("grep x | head") == "forbidden"


def test_custom_allow_does_not_remove_read_only_commands():
    assert ShellPolicy(["npm test"]).is_allowed("ls")


def test_refusal_detail_for_containment(tmp_path):
    policy = ShellPolicy([], root=tmp_path)
    assert policy.refusal_detail("cat /etc/passwd") == "stay inside the worktree"


def test_refusal_detail_is_none_when_allowed_or_only_not_allowed():
    assert ShellPolicy([]).refusal_detail("ls") is None
    assert ShellPolicy([]).refusal_detail("npm run build") is None


def test_literal_pattern_matches_only_the_exact_command():
    exact = "pytest tests/test_foo.py::test_bar[case1]"
    policy = ShellPolicy([literal_pattern(exact), literal_pattern("pytest tests/*")])
    assert policy.is_allowed(exact)
    assert not policy.is_allowed("pytest tests/test_foo.py::test_bar[XYZ9]")
    assert policy.is_allowed("pytest tests/*")
    assert not policy.is_allowed("pytest tests/anything.py")


def test_kill_active_groups_stops_running_commands(tmp_path):
    results = []
    thread = threading.Thread(
        target=lambda: results.append(
            run_command(f'{PY} -c "import time; time.sleep(30)"', cwd=tmp_path, timeout_s=60)
        )
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not shell_module._ACTIVE_ROOTS and time.monotonic() < deadline:
        time.sleep(0.05)
    assert shell_module._ACTIVE_ROOTS
    killed = shell_module.kill_active_groups()
    thread.join(timeout=10)
    assert killed
    assert results and not results[0].ok
    assert not shell_module._ACTIVE_ROOTS


def _spawns_a_sleeping_child(pid_file) -> str:
    """A command whose process starts a sleeping child, writes the child's pid, then sleeps."""
    code = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"open({str(pid_file)!r}, 'w').write(str(child.pid)); "
        "time.sleep(60)"
    )
    return f"{PY} -c {shlex.quote(code)}"


def _wait_for_pid(pid_file) -> int:
    deadline = time.monotonic() + 10
    while not (pid_file.exists() and pid_file.read_text()) and time.monotonic() < deadline:
        time.sleep(0.05)
    return int(pid_file.read_text())


def test_a_timed_out_command_leaves_no_child_alive(tmp_path):
    pid_file = tmp_path / "child_pid.txt"
    result = run_command(_spawns_a_sleeping_child(pid_file), cwd=tmp_path, timeout_s=2)
    assert result.timed_out
    child_pid = _wait_for_pid(pid_file)
    deadline = time.monotonic() + 5
    while platform.pid_alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not platform.pid_alive(child_pid)


def test_kill_active_groups_kills_the_whole_tree(tmp_path):
    pid_file = tmp_path / "child_pid.txt"
    results = []
    thread = threading.Thread(
        target=lambda: results.append(run_command(_spawns_a_sleeping_child(pid_file), cwd=tmp_path, timeout_s=60))
    )
    thread.start()
    child_pid = _wait_for_pid(pid_file)
    killed = shell_module.kill_active_groups()
    thread.join(timeout=10)
    assert killed
    assert results and not results[0].ok
    # The killed command's own exit status comes through, never a reaped-away 0.
    assert results[0].exit_code != 0
    if not platform.IS_WINDOWS:
        assert results[0].exit_code == -9
    deadline = time.monotonic() + 5
    while platform.pid_alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not platform.pid_alive(child_pid)


class SignalReceived(BaseException):
    """Custom exception raised by signal handler."""
    pass


def _double_forks_a_sleeper(pid_file, *, new_session: bool) -> str:
    """A command that prints, then double-forks a sleeper that inherits (so holds open) its
    stdout and is reparented to init; the sleeper writes its pid. With `new_session` the
    sleeper also leaves the command's process group."""
    code = "\n".join([
        "import os, sys, time",
        "print('started', flush=True)",
        "if os.fork() == 0:",
        "    if os.fork() == 0:",
        f"        {'os.setsid()' if new_session else 'pass'}",
        f"        open({str(pid_file)!r}, 'w').write(str(os.getpid()))",
        "        time.sleep(60)",
        "    os._exit(0)",
        "time.sleep(60)",
    ])
    return f"{PY} -c {shlex.quote(code)}"


@pytest.mark.skipif(platform.IS_WINDOWS, reason="os.fork is POSIX-only")
def test_a_timeout_kills_a_double_forked_sleeper_in_the_group(tmp_path):
    pid_file = tmp_path / "sleeper_pid.txt"
    started = time.monotonic()
    result = run_command(_double_forks_a_sleeper(pid_file, new_session=False), cwd=tmp_path, timeout_s=2)
    assert time.monotonic() - started < 2 + shell_module.POST_KILL_WAIT_S
    assert result.timed_out
    assert result.exit_code == -9
    assert "started" in result.stdout
    assert not platform.pid_alive(_wait_for_pid(pid_file))


@pytest.mark.skipif(platform.IS_WINDOWS, reason="os.fork is POSIX-only")
def test_a_timeout_returns_even_if_an_escaped_sleeper_holds_the_output(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_module, "POST_KILL_WAIT_S", 1.0)
    pid_file = tmp_path / "sleeper_pid.txt"
    started = time.monotonic()
    try:
        result = run_command(_double_forks_a_sleeper(pid_file, new_session=True), cwd=tmp_path, timeout_s=2)
        assert time.monotonic() - started < 10
        assert result.timed_out
        assert result.exit_code == -9
        assert "started" in result.stdout
    finally:
        if pid_file.exists() and pid_file.read_text():
            platform.kill_tree(int(pid_file.read_text()))


@pytest.mark.skipif(platform.IS_WINDOWS, reason="SIGALRM is POSIX-only")
def test_run_command_kills_child_on_sigalrm(tmp_path):
    """Test that child processes are killed even if a BaseException interrupts run_command."""
    pid_file = tmp_path / "child_pid.txt"

    class SignalTestException(BaseException):
        pass

    def handler(signum, frame):
        raise SignalTestException("Signal received")

    # Install handler and set timer
    old_handler = signal.signal(signal.SIGALRM, handler)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.5)

        # Python script that writes its pid and sleeps
        script = f'{PY} -c "import os; open({str(pid_file)!r}, \'w\').write(str(os.getpid())); import time; time.sleep(30)"'

        # Expect the exception to be raised
        with pytest.raises(SignalTestException):
            run_command(script, cwd=tmp_path, timeout_s=60)

        # Read child pid and verify it's dead
        child_pid = int(pid_file.read_text())
        assert not platform.pid_alive(child_pid), f"Child process {child_pid} should be dead"

        # Verify registry is empty
        assert not shell_module._ACTIVE_ROOTS
    finally:
        # Clean up: cancel timer and restore old handler
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def test_git_ls_files_is_read_only_and_contained(tmp_path):
    (tmp_path / "src").mkdir()
    policy = ShellPolicy([], root=tmp_path)
    assert policy.is_allowed("git ls-files")
    assert policy.is_allowed("git ls-files src")
    assert policy.denial_reason("git ls-files /etc") == "forbidden"


@pytest.mark.parametrize(
    "command",
    [
        "find . -files0-from list.txt",
        "find -files0-from list.txt",
        "wc --files0-from=list.txt",
        "wc --files0-from list.txt",
        "wc --files0 list.txt",
    ],
)
def test_files0_from_is_forbidden(command):
    assert ShellPolicy([]).denial_reason(command) == "forbidden"


def test_wc_plain_count_is_still_allowed():
    assert ShellPolicy([]).is_allowed("wc -l README.md")


def test_normalise_path_text_turns_backslashes_into_slashes():
    assert normalise_path_text(r"'C:\x\python.exe' a.py") == "'C:/x/python.exe' a.py"
    assert normalise_path_text("pytest -q tests/a.py") == "pytest -q tests/a.py"


WINDOWS_COMMAND = r"'C:\x\python.exe' a.py"


@pytest.mark.parametrize("pattern", [r"'C:\x\python.exe' *", "C:/x/python.exe *"])
def test_on_windows_a_backslash_path_matches_either_allow_pattern_style(monkeypatch, pattern):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    assert ShellPolicy([pattern]).is_allowed(WINDOWS_COMMAND)


@pytest.mark.parametrize(
    ("command", "allow"),
    [
        (r"find . \-delete", []),
        (r"python3 \-c x", ["python3 *"]),
        (r"git \-c a=b log", ["git *"]),
    ],
)
def test_on_windows_an_escaped_flag_is_checked_as_it_runs(monkeypatch, command, allow):
    # shlex turns `\-delete` into `-delete` for the command that runs: the checks must see that,
    # not a normalised `/-delete`.
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    assert ShellPolicy(allow).denial_reason(command) == "forbidden"


@pytest.mark.parametrize("plan_command", [r"'C:\x\python.exe' a.py", "C:/x/python.exe a.py"])
def test_on_windows_a_backslash_path_matches_either_extra_allow_style(monkeypatch, plan_command):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    assert ShellPolicy([], extra_allow=(plan_command,)).is_allowed(WINDOWS_COMMAND)


@pytest.mark.parametrize("approved", [r"'C:\x\python.exe' a.py", "C:/x/python.exe a.py"])
def test_on_windows_a_backslash_path_matches_either_approved_style(monkeypatch, approved):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    assert ShellPolicy([], approved=(approved,)).is_allowed(WINDOWS_COMMAND)


@pytest.mark.parametrize("command", [r"cat '..\secret.txt'", r"cat '-f..\secret.txt'", r"ls 'sub\..\..\x'"])
def test_on_windows_a_backslash_path_outside_the_worktree_is_refused(tmp_path, monkeypatch, command):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    policy = ShellPolicy([], root=tmp_path / "worktree")
    assert policy.refusal_detail(command) == "stay inside the worktree"


def test_on_windows_a_backslash_path_inside_the_worktree_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    assert ShellPolicy([], root=tmp_path).is_allowed(r"cat 'src\a.py'")


@pytest.mark.parametrize(
    "command",
    [
        "C:/x/python3.exe -c 'print(1)'",
        r"'C:\x\Python.EXE' -c 'print(1)'",
        "python.exe -c 'print(1)'",
        "npm.cmd --prefix=/tmp install",
        "git.exe -c core.pager=x log",
    ],
)
def test_on_windows_risky_programs_are_matched_by_basename(monkeypatch, command):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    assert ShellPolicy(["* *"]).denial_reason(command) == "forbidden"


def test_on_posix_risky_program_matching_is_unchanged(monkeypatch):
    monkeypatch.setattr(platform, "IS_WINDOWS", False)
    assert ShellPolicy(["python.exe *"]).is_allowed("python.exe -c x")


def test_on_windows_the_pipes_are_not_closed_after_the_bound(monkeypatch):
    # communicate()'s reader threads hold each pipe's lock on Windows, so close() would hang.
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(platform, "kill_tree", lambda pid: None)
    closed = []

    class Pipe:
        def close(self):
            closed.append(self)

    class Proc:
        pid, stdout, stderr = 4242, Pipe(), Pipe()

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired("cmd", timeout)

        def wait(self, timeout=None):
            return -9

    assert shell_module._output_after_kill(Proc()) == ("", "")
    assert closed == []


def test_on_posix_backslashes_are_not_normalised_for_matching(monkeypatch):
    monkeypatch.setattr(platform, "IS_WINDOWS", False)
    assert not ShellPolicy(["C:/x/python.exe *"]).is_allowed(WINDOWS_COMMAND)


def test_on_posix_a_backslash_reaches_the_command_intact(tmp_path, monkeypatch):
    monkeypatch.setattr(platform, "IS_WINDOWS", False)
    result = run_command(r"printf '%s' 'a\.b'", cwd=tmp_path, timeout_s=10)
    assert result.ok, result.stderr
    assert result.stdout == r"a\.b"


class FakePopen:
    """Stands in for subprocess.Popen: records the argv and finishes at once."""

    calls: ClassVar[list[list[str]]] = []

    def __init__(self, args, **kwargs):
        FakePopen.calls.append(args)
        self.pid = 4242
        self.returncode = 0

    def communicate(self, timeout=None):
        return "", ""


@pytest.fixture
def fake_windows(monkeypatch):
    """Windows code paths on any OS: IS_WINDOWS, a found bash, no real process spawned."""
    found: list[str | None] = []

    def find_bash(configured=None, **_):
        found.append(configured)
        return r"C:\Git\bin\bash.exe"

    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(platform, "find_bash", find_bash)
    monkeypatch.setattr(platform, "detach_kwargs", dict)
    FakePopen.calls = []
    monkeypatch.setattr(shell_module.subprocess, "Popen", FakePopen)
    return found


def test_on_windows_a_command_runs_through_bash(tmp_path, fake_windows):
    result = run_command("npm test -- 'a b'", cwd=tmp_path, timeout_s=10)
    assert result.ok
    assert FakePopen.calls == [[r"C:\Git\bin\bash.exe", "-c", "npm test -- 'a b'"]]


def test_on_windows_the_argv_keeps_its_backslashes(tmp_path, fake_windows):
    run_command(WINDOWS_COMMAND, cwd=tmp_path, timeout_s=10)
    assert FakePopen.calls == [[r"C:\Git\bin\bash.exe", "-c", r"'C:\x\python.exe' a.py"]]


def test_on_windows_the_configured_bash_is_looked_up(tmp_path, fake_windows):
    run_command("npm test", cwd=tmp_path, timeout_s=10, bash=r"D:\Git\bin\bash.exe")
    assert fake_windows == [r"D:\Git\bin\bash.exe"]


def test_on_windows_without_bash_the_command_cannot_run(tmp_path, fake_windows, monkeypatch):
    monkeypatch.setattr(platform, "find_bash", lambda configured=None, **_: None)
    result = run_command("npm test", cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 127
    assert platform.MISSING_BASH in result.stderr
    assert FakePopen.calls == []
