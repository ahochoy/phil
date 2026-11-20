from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.measure import Measurement

from phil.ui.theme import PHIL_THEME


class Themed:
    """`renderable`, rendered under `PHIL_THEME` so its `phil.*` style names resolve.

    Textual renders Rich renderables on its own console, which doesn't know Phil's style names;
    every conversation item is wrapped in this. The segments are rendered eagerly inside the theme,
    so the pushed theme never outlives this render."""

    def __init__(self, renderable: RenderableType) -> None:
        self.renderable = renderable

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        with console.use_theme(PHIL_THEME):
            segments = list(console.render(self.renderable, options))
        yield from segments

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        with console.use_theme(PHIL_THEME):
            return Measurement.get(console, options, self.renderable)
