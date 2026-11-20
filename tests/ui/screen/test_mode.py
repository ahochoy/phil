import pytest

from phil.ui.screen.mode import choose_mode, windows_console_ok


@pytest.mark.parametrize("args, expected", [
    (("full", False, 120, 40, True), ("line", None)),
    (("inline", True, 120, 40, True), ("inline", None)),
    (("full", True, 59, 40, True), ("inline", "This terminal is smaller than 60×15; using the inline chat.")),
    (("full", True, 120, 14, True), ("inline", "This terminal is smaller than 60×15; using the inline chat.")),
    (("full", True, 120, 40, False), ("inline", "This console can't show the full-screen chat; using the inline chat.")),
    (("full", True, 60, 15, True), ("full", None)),
])
def test_choose_mode(args, expected):
    assert choose_mode(*args) == expected


@pytest.mark.parametrize("is_windows, environ, expected", [
    (False, {}, True),
    (True, {}, False),
    (True, {"WT_SESSION": "1"}, True),
    (True, {"TERM_PROGRAM": "vscode"}, True),
    (True, {"WT_SESSION": ""}, False),
])
def test_windows_console_ok(is_windows, environ, expected):
    assert windows_console_ok(is_windows, environ) is expected
