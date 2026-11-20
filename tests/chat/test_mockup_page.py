import html

from phil.chat.mockup_page import (
    CSP,
    browser_available,
    build_page,
    clean_fragment,
    comparing_line,
    write_page,
)

CLOSING = "Pick in Phil: Enter or a number to choose, `show N <note>` to redraw."


def test_scripts_handlers_and_js_urls_are_removed():
    html = (
        "<div><SCRIPT>x</script>"
        "<script src=a>"
        '<img onload="x" onerror=y>'
        '<a href="javascript:alert(1)">go</a></div>'
    )
    out = clean_fragment(html).lower()
    for banned in ("script", "onload", "onerror", "javascript:"):
        assert banned not in out
    assert "<img" in out
    assert "<a" in out


def test_clean_fragment_handles_crlf_line_endings():
    html = '<div>\r\n<script>\r\nalert(1)\r\n</script>\r\n<p onclick="x()">hi</p>\r\n</div>'
    out = clean_fragment(html)
    assert "script" not in out.lower()
    assert "onclick" not in out.lower()
    assert "<p" in out and "hi" in out


def test_page_carries_the_csp_first():
    page = build_page("add a header", "compare", [(1, "Top bar", "<div>x</div>")])
    assert CSP in page
    assert page.index("Content-Security-Policy") < page.index("<style")
    assert page.index("<meta http-equiv") < page.index("<style")


def test_page_escapes_names_and_objective():
    page = build_page("<b>x</b>", "compare", [(1, "a&b", "<div>x</div>")])
    assert "&lt;b&gt;x&lt;/b&gt;" in page
    assert "<h1><b>x</b></h1>" not in page
    assert "1. a&amp;b" in page


def test_page_ends_with_how_to_respond():
    page = build_page("obj", "compare", [(1, "A", "<div>x</div>")])
    # `<note>` must be escaped to show in the browser.
    assert html.escape(CLOSING) in page


def test_page_has_wireframe_classes_and_stacking_rule():
    page = build_page("obj", "compare", [(1, "A", "<div>x</div>")])
    for cls in ("mock-nav", "mock-sidebar", "mock-content", "mock-button", "mock-input", "placeholder", "split"):
        assert f".{cls}" in page
    assert "800px" in page


def test_comparing_line():
    assert comparing_line(3, "add search") == "3 approaches to add search: compare placement and content."
    assert comparing_line(1, "add search") == "1 approach to add search: compare placement and content."


def test_browser_rule():
    assert browser_available({}, "darwin") is True
    assert browser_available({"SSH_TTY": "/dev/pts/0"}, "darwin") is False
    assert browser_available({"SSH_CONNECTION": "a b c d"}, "darwin") is False
    assert browser_available({}, "linux") is False
    assert browser_available({"DISPLAY": ":0"}, "linux") is True
    assert browser_available({"WAYLAND_DISPLAY": "wayland-0"}, "linux") is True
    assert browser_available({}, "win32") is True


def test_write_page_versions_and_first_flag(tmp_path):
    folder = tmp_path / "mockups"
    path, first = write_page(folder, "chat1", "<p>one</p>")
    assert first is True
    assert path == folder / "chat1.html"
    assert (folder / "chat1-v1.html").read_text(encoding="utf-8") == "<p>one</p>"

    path, first = write_page(folder, "chat1", "<p>two</p>")
    assert first is False
    assert (folder / "chat1-v2.html").read_text(encoding="utf-8") == "<p>two</p>"
    assert path.read_text(encoding="utf-8") == "<p>two</p>"


def test_write_page_does_not_collide_across_chats(tmp_path):
    write_page(tmp_path, "a", "A")
    _, first = write_page(tmp_path, "b", "B")
    assert first is True
