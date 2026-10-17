import io

from rich.console import Console

from phil.ui.links import opened_pr_line

URL = "https://github.com/example/repo/pull/12"


def render(text, *, terminal: bool) -> str:
    out = io.StringIO()
    Console(file=out, force_terminal=terminal, color_system="truecolor" if terminal else None, width=120).print(text)
    return out.getvalue()


def test_on_a_terminal_the_url_is_a_hyperlink_to_itself():
    shown = render(opened_pr_line(12, URL), terminal=True)
    assert "\x1b]8;id=" in shown and f";{URL}\x1b\\{URL}" in shown
    assert "Opened PR #12: " in shown


def test_off_a_terminal_it_is_plain_text():
    assert render(opened_pr_line(12, URL), terminal=False) == f"Opened PR #12: {URL}\n"


def test_markup_and_escapes_in_a_url_are_shown_not_run():
    shown = render(opened_pr_line(3, "https://x.test/[bold]a\x1b[2J"), terminal=False)
    assert shown == "Opened PR #3: https://x.test/[bold]a?[2J\n"
