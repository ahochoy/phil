"""Helpers for driving the full-screen frame under Textual's test driver from sync tests."""

import asyncio
import time

from textual.geometry import Region
from textual.widget import Widget


def pilot_run(app, script, size=(100, 30)):
    """Run `script(pilot)` (async) against `app` under Textual's test driver, from a sync test."""

    async def main():
        async with app.run_test(size=size) as pilot:
            await script(pilot)

    asyncio.run(main())


async def until(pilot, predicate, timeout=5.0):
    """Pause the pilot until `predicate()` is true; fail the test after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {predicate}")
        await pilot.pause(0.01)


def render(widget: Widget) -> str:
    """The text `widget` renders (its own lines, then its displayed children's), one line per row."""
    lines = []
    width, height = widget.outer_size  # the size with padding and border, which render_lines covers
    if not widget.children and width and height:
        lines = [strip.text.rstrip() for strip in widget.render_lines(Region(0, 0, width, height))]
    for child in widget.children:
        if child.display:
            lines.append(render(child))
    return "\n".join(lines)
