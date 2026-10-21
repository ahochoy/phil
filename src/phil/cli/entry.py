"""Phil's console entry point: diagnostics first, then the CLI.

Nothing heavy is imported at module level, so warnings raised while the CLI's libraries import
are already captured.
"""

import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from phil import diagnostics
from phil.store.paths import phil_home


def _log_path() -> Path:
    return phil_home() / "logs" / "phil.log"


def main() -> None:
    diagnostics.configure(_log_path())

    def load():
        from phil.cli.main import app

        return app

    run(load)


def run(load_app: Callable) -> None:
    """Load and run the CLI; an uncaught exception becomes one line and a crash file.

    With PHIL_DEBUG=1 the exception is re-raised instead, for Python's own traceback.
    """
    if not diagnostics.configured():
        diagnostics.configure(_log_path())
    app = None
    try:
        app = load_app()
        app()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        if diagnostics.debug_enabled():
            raise
        command = command_path(app, sys.argv[1:]) if app is not None else None
        _print_err(diagnostics.crash_message(exc, diagnostics.crash(exc, command)))
        raise SystemExit(1) from None


def command_path(app, argv: Sequence[str]) -> str | None:
    """The subcommand names in `argv` (`keys set`), never option or argument values."""
    import typer.main

    try:
        group = typer.main.get_command(app)
    except Exception:
        return None
    names = []
    for token in argv:
        commands = getattr(group, "commands", None)
        if not commands:
            break
        if token in commands:
            names.append(token)
            group = commands[token]
    return " ".join(names) or None


def _print_err(text: str, stream: TextIO | None = None) -> None:
    """Print to stderr, replacing what its encoding can't show rather than failing."""
    stream = stream if stream is not None else sys.stderr
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding), file=stream)
