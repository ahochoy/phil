from rich.text import Text

Fragments = list[tuple[str, str]]


def _style(style: str) -> str:
    """prompt_toolkit's `class:phil.x` becomes Rich's `phil.x`; plain attribute words pass through."""
    return style.removeprefix("class:")


def to_text(fragments: Fragments) -> Text:
    """prompt_toolkit-style fragments as Rich `Text`, for widgets that draw them."""
    text = Text()
    for style, chunk in fragments:
        text.append(chunk, style=_style(style) or None)
    return text
