import io
import locale
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import typer

import phil
from phil.cli.entry import _print_err, command_path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import logging, sys, warnings
import typer
from phil.cli.entry import run

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)

@app.command()
def warn():
    warnings.warn("a library warning", UserWarning)

@app.command()
def log():
    logging.getLogger("somelib").warning("a library log line")

@app.command()
def boom():
    planted_secret = "sk-planted-123"
    raise RuntimeError("it broke")

@app.command()
def interrupt():
    raise KeyboardInterrupt

def broken_loader():
    raise ImportError("a broken install")

run(broken_loader if sys.argv[1:] == ["load-fails"] else (lambda: app))
"""


def launch(home, *args, debug=False):
    env = {k: v for k, v in os.environ.items() if k != "PHIL_DEBUG"}
    env["PHIL_HOME"] = str(home)
    if debug:
        env["PHIL_DEBUG"] = "1"
    # A child writing to a pipe uses the locale's encoding (cp1252 on Windows); -I makes it ignore
    # PYTHONUTF8, so read its output the same way.
    return subprocess.run(
        [sys.executable, "-I", "-c", SCRIPT, *args],
        env=env,
        capture_output=True,
        text=True,
        encoding=locale.getencoding(),
    )


def crash_files(home):
    return sorted((home / "logs").glob("crash-*.log"))


def test_a_library_warning_stays_off_the_terminal(tmp_path):
    result = launch(tmp_path, "warn")
    assert result.returncode == 0 and result.stderr == ""
    assert "a library warning" in (tmp_path / "logs" / "phil.log").read_text(encoding="utf-8")


def test_a_library_log_line_stays_off_the_terminal(tmp_path):
    result = launch(tmp_path, "log")
    assert result.returncode == 0 and result.stderr == ""
    assert "a library log line" in (tmp_path / "logs" / "phil.log").read_text(encoding="utf-8")


def test_debug_shows_warnings_and_log_lines(tmp_path):
    assert "a library warning" in launch(tmp_path, "warn", debug=True).stderr
    assert "a library log line" in launch(tmp_path, "log", debug=True).stderr


def test_a_crash_is_one_line_and_a_file(tmp_path):
    result = launch(tmp_path, "boom")
    [path] = crash_files(tmp_path)
    assert result.returncode == 1
    assert result.stderr == (
        f"Phil hit an unexpected error (RuntimeError). Details were saved to {path}"
        " — please include it if you report this.\n"
    )
    text = path.read_text(encoding="utf-8")
    assert "RuntimeError: it broke" in text and "Command: boom" in text
    assert "sk-planted-123" not in text


def test_debug_shows_the_traceback_without_locals(tmp_path):
    result = launch(tmp_path, "boom", debug=True)
    assert result.returncode == 1
    assert "Traceback" in result.stderr and "RuntimeError: it broke" in result.stderr
    assert "sk-planted-123" not in result.stderr
    assert crash_files(tmp_path) == []


def test_an_interrupt_exits_quietly(tmp_path):
    result = launch(tmp_path, "interrupt")
    assert result.returncode == 130 and result.stderr == ""
    assert crash_files(tmp_path) == []


def test_a_crash_while_loading_the_cli_is_one_line(tmp_path):
    result = launch(tmp_path, "load-fails")
    [path] = crash_files(tmp_path)
    assert result.returncode == 1
    assert result.stderr.startswith("Phil hit an unexpected error (ImportError). Details were saved to ")
    assert "Command: (none)" in path.read_text(encoding="utf-8")


def test_the_command_path_keeps_only_subcommand_names():
    app = typer.Typer()
    keys = typer.Typer()
    app.add_typer(keys, name="keys")

    @keys.command("set")
    def set_key(name: str, value: str) -> None: ...

    @app.command()
    def runs() -> None: ...

    argv = ["--repo", "/x", "keys", "set", "OPENROUTER", "sk-abc"]
    assert command_path(app, argv) == "keys set"
    assert command_path(app, ["--version"]) is None


def test_the_crash_line_survives_a_stderr_that_cannot_encode_it():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    _print_err("Phil hit an unexpected error (KeyError). Details — x → y", stream)
    stream.flush()
    assert raw.getvalue().decode("cp1252").startswith("Phil hit an unexpected error (KeyError).")


def test_the_console_script_is_the_entry_point():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["phil"] == "phil.cli.entry:main"


def test_python_m_phil_still_prints_the_version(tmp_path):
    env = {**os.environ, "PHIL_HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "phil", "--version"], env=env, capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0 and result.stdout.strip() == phil.__version__
    assert result.stderr == ""
