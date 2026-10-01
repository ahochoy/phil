"""Setup's input and output: a terminal implementation, and a scripted one for tests."""

import getpass
import sys
from typing import Protocol

from rich.console import Console
from rich.markup import escape


class SetupCancelled(Exception):
    """Ctrl-C or end of input at a setup prompt."""


class SetupIO(Protocol):
    def say(self, text: str) -> None: ...

    def ask(self, prompt: str, default: str | None = None) -> str:
        """A line of text; Enter alone gives `default` (or "" without one)."""
        ...

    def choose(self, prompt: str, options: list[str], default: int = 0) -> int:
        """The index of the chosen option; Enter alone gives `default`."""
        ...

    def secret(self, prompt: str) -> str:
        """A line typed without echo."""
        ...


def _ask_text(prompt: str, default: str | None) -> str:
    return f"{prompt} [{default}]: " if default else f"{prompt}: "


def _pick(answer: str, options: list[str], default: int) -> int | None:
    """The option `answer` names: Enter for `default`, a 1-based number, or the start of an
    option's text (ignoring case). None if it names none."""
    answer = answer.strip()
    if not answer:
        return default
    if answer.isdigit():
        index = int(answer) - 1
        return index if 0 <= index < len(options) else None
    for index, option in enumerate(options):
        if option.lower().startswith(answer.lower()):
            return index
    return None


class TerminalSetupIO:
    """Prompts on a prompt_toolkit session in a terminal, or plain `input()` when stdin isn't one."""

    def __init__(self, console: Console, *, interactive: bool | None = None) -> None:
        self.console = console
        self.interactive = sys.stdin.isatty() if interactive is None else interactive
        self._session = None

    def say(self, text: str) -> None:
        self.console.print(escape(text), soft_wrap=True, highlight=False)

    def _read(self, text: str) -> str:
        try:
            if not self.interactive:
                return input(text)
            if self._session is None:
                from prompt_toolkit import PromptSession

                self._session = PromptSession()
            return self._session.prompt(text)
        except (KeyboardInterrupt, EOFError) as exc:
            raise SetupCancelled from exc

    def ask(self, prompt: str, default: str | None = None) -> str:
        answer = self._read(_ask_text(prompt, default)).strip()
        return answer or (default or "")

    def choose(self, prompt: str, options: list[str], default: int = 0) -> int:
        self.say(prompt)
        for number, option in enumerate(options, 1):
            self.say(f"  {number}. {option}")
        while True:
            index = _pick(self._read(f"Choose 1-{len(options)} [{default + 1}]: "), options, default)
            if index is not None:
                return index
            self.say(f"Enter a number from 1 to {len(options)}.")

    def secret(self, prompt: str) -> str:
        try:
            if not self.interactive:
                return input(f"{prompt}: ").strip()
            return getpass.getpass(f"{prompt}: ").strip()
        except (KeyboardInterrupt, EOFError) as exc:
            raise SetupCancelled from exc


class ScriptedSetupIO:
    """Answers from a list, for tests. `lines` records everything said (and each choice's
    options), `prompts` each input asked for as (kind, prompt). Running out of answers cancels."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.lines: list[str] = []
        self.prompts: list[tuple[str, str]] = []

    def say(self, text: str) -> None:
        self.lines.append(text)

    def _next(self, kind: str, prompt: str) -> str:
        self.prompts.append((kind, prompt))
        if not self.answers:
            raise SetupCancelled
        return self.answers.pop(0)

    def ask(self, prompt: str, default: str | None = None) -> str:
        return self._next("ask", prompt).strip() or (default or "")

    def choose(self, prompt: str, options: list[str], default: int = 0) -> int:
        self.say(prompt)
        self.lines.extend(f"  {number}. {option}" for number, option in enumerate(options, 1))
        answer = self._next("choose", prompt)
        index = _pick(answer, options, default)
        if index is None:
            raise AssertionError(f"scripted answer {answer!r} names none of {options}")
        return index

    def secret(self, prompt: str) -> str:
        return self._next("secret", prompt).strip()
