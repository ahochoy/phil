"""The full-screen chat end to end: the real `ScreenIO` (its `phil-chat` worker thread, the real
frame under Textual's test driver) driving the real CLI driver and the scripted controller."""

import asyncio

import pytest
from typer.testing import CliRunner

from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs
from phil.ui.screen.io import ScreenIO
from phil.ui.screen.widgets import Conversation, DecisionMenu
from tests.helpers import classic_repo
from tests.ui.screen.pilot import render, until

runner = CliRunner()


def _pilot_screen(actions, size=(100, 30)):
    """A `ScreenIO` whose `run` drives the frame under `run_test`, playing `actions` (each
    `async act(io, pilot)`) and then waiting for the chat to end."""

    class PilotScreen(ScreenIO):
        def run(self, fn):
            self._run_fn = fn

            async def main():
                async with self.app.run_test(size=size) as pilot:
                    for act in actions:
                        await act(self, pilot)
                    await until(pilot, lambda: self._future is not None and self._future.done(), timeout=10)

            asyncio.run(main())
            return self._future.result()

    return PilotScreen


async def _asking(io, pilot):
    await until(pilot, lambda: io.asking, timeout=10)


def _type(text):
    async def act(io, pilot):
        await _asking(io, pilot)
        await pilot.press(*text, "enter")
        await pilot.pause(0.1)

    return act


def _menu(key):
    async def act(io, pilot):
        try:
            await until(pilot, lambda: io.app.query_one(DecisionMenu).decision is not None, timeout=10)
        except AssertionError as exc:  # say what the chat showed instead of the menu
            raise AssertionError(f"no menu; the conversation:\n{render(io.app.query_one(Conversation))}") from exc
        await pilot.press(key)
        await until(pilot, lambda: io.app.query_one(DecisionMenu).decision is None, timeout=10)

    return act


def _snapshot(seen: list[str]):
    async def act(io, pilot):
        await _asking(io, pilot)
        await pilot.pause(0.1)
        seen.append(render(io.app.query_one(Conversation)))

    return act


async def _eof(io, pilot):
    await _asking(io, pilot)
    await pilot.press("ctrl+d")


@pytest.fixture
def full_screen(calc_repo, monkeypatch, phil_home):
    """Opt the chat into the real full screen with the scripted `approve` scenario; returns a
    function that sets the frame's actions (and its size)."""
    phil_home.mkdir(parents=True, exist_ok=True)
    (phil_home / "config.toml").write_text('[chat]\nscreen = "full"\n', encoding="utf-8")
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.chat.chat_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "approve")
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    # On Windows the legacy console falls back to inline; these tests stand in Windows Terminal.
    monkeypatch.setenv("WT_SESSION", "phil-tests")
    classic_repo(calc_repo)

    def play(actions, size=(100, 30)):
        monkeypatch.setattr(cli, "_screen", _pilot_screen(actions, size))
        monkeypatch.setenv("COLUMNS", str(size[0]))
        monkeypatch.setenv("LINES", str(size[1]))

    return play


def _card(conversation: str) -> list[str]:
    """The banner card's rows: from its top border to its bottom one."""
    lines = conversation.splitlines()
    top = next(i for i, line in enumerate(lines) if line.startswith("╭"))
    bottom = next(i for i, line in enumerate(lines) if line.startswith("╰"))
    return lines[top:bottom + 1]


@pytest.mark.parametrize("columns", [60, 72])
def test_the_banner_card_fits_a_narrow_pane(calc_repo, full_screen, columns):
    seen: list[str] = []
    full_screen([_snapshot(seen), _eof], size=(columns, 20))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)])
    assert result.exit_code == 0, f"{result.output}\n{result.exception}"
    card = _card(seen[0])
    assert len(card) > 2 and all(line.rstrip()[-1] in "╮│╯" for line in card), "\n".join(card)
    assert all(line[0] in "╭│╰" for line in card)  # no row wrapped onto the next line
    assert "Phil v" in seen[0]


def test_the_banner_card_reflows_when_the_pane_resizes(calc_repo, full_screen):
    seen: list[str] = []

    async def narrow(io, pilot):
        await pilot.resize_terminal(62, 20)
        await pilot.pause(0.2)

    full_screen([_snapshot(seen), narrow, _snapshot(seen), _eof], size=(100, 20))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)])
    assert result.exit_code == 0, f"{result.output}\n{result.exception}"
    wide, narrowed = (_card(conversation) for conversation in seen)
    assert all(line.rstrip()[-1] in "╮│╯" for line in narrowed), "\n".join(narrowed)
    assert max(map(len, narrowed)) < 62 < max(map(len, wide))


def test_a_goal_runs_on_the_frames_worker_thread(calc_repo, full_screen):
    """Regression: the controller's connection was opened on the main thread and used on
    `phil-chat`, so every goal failed with SQLite's ProgrammingError."""
    seen: list[str] = []
    full_screen([_type("add subtract"), _menu("enter"), _type("/chat"), _snapshot(seen), _eof])
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)])
    assert result.exit_code == 0, f"{result.output}\n{result.exception}"
    [conversation] = seen
    assert "ProgrammingError" not in conversation and "Something went wrong" not in conversation
    assert "No other open chats." in conversation  # /chat read the db on the worker thread too
    assert "add subtract" in conversation and "/chat" in conversation  # the user's lines are echoed
    paths = ProjectPaths(resolve_repo(calc_repo).slug)
    [run] = list_runs(connect(paths.db_path))
    assert run.chat_id is not None
    assert result.output.rstrip().endswith(f"To continue this chat:  phil --resume {run.chat_id}")
