import html

from phil.chat.mockup_page import (
    CSP,
    browser_available,
    build_page,
    clean_fragment,
    approach_slug,
    comparing_line,
    write_drawing,
    write_page,
)

CLOSING = "Pick in Phil: Enter or a number to choose, `show N <note>` to redraw."


def test_csp_is_exact():
    assert CSP == (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
        "form-action 'none'; base-uri 'none'"
    )


def test_scripts_handlers_and_js_urls_are_removed():
    fragment = (
        "<div><SCRIPT>x</script>"
        "<script src=a>"
        '<img onload="x" onerror=y>'
        '<a href="javascript:alert(1)">go</a></div>'
    )
    out = clean_fragment(fragment).lower()
    for banned in ("script", "onload", "onerror", "javascript:"):
        assert banned not in out
    assert "<img" in out
    assert "<a" in out


def test_clean_fragment_handles_crlf_line_endings():
    fragment = '<div>\r\n<script>\r\nalert(1)\r\n</script>\r\n<p onclick="x()">hi</p>\r\n</div>'
    out = clean_fragment(fragment)
    assert "script" not in out.lower()
    assert "onclick" not in out.lower()
    assert "<p" in out and "hi" in out


def test_meta_refresh_is_stripped():
    out = clean_fragment('<div><META http-equiv=refresh content="0;url=https://e"><p>x</p></div>')
    assert "<meta" not in out.lower()
    assert "refresh" not in out.lower()
    assert "<p>x</p>" in out


def test_form_is_stripped():
    out = clean_fragment("<FORM action=https://e><input></form>")
    assert "form" not in out.lower()
    assert "https://e" not in out
    assert "<input" in out


def test_base_and_link_are_stripped():
    out = clean_fragment('<base href="https://e"><link rel=stylesheet href=https://e/x.css><p>x</p>')
    assert "<base" not in out.lower()
    assert "<link" not in out.lower()
    assert "https://e" not in out


def test_iframe_object_embed_are_stripped():
    out = clean_fragment(
        "<iframe src=https://e>inner</iframe><OBJECT data=x>o</object><embed src=y><iframe src=z><p>k</p>"
    )
    low = out.lower()
    for tag in ("<iframe", "<object", "<embed"):
        assert tag not in low
    assert "<p>k</p>" in out


def test_external_urls_lose_their_attribute():
    out = clean_fragment('<a href="https://e">a</a><a href=//e/x>b</a><img src= "http://e/i.png">')
    assert "href" not in out.lower()
    assert "src" not in out.lower()
    assert "https://e" not in out
    assert "<a" in out and "<img" in out


def test_data_and_fragment_urls_are_kept():
    out = clean_fragment('<img src="data:image/png;base64,AA"><a href="#top">t</a><img SRC=" DATA:image/png,x">')
    assert 'src="data:image/png;base64,AA"' in out
    assert 'href="#top"' in out
    assert out.lower().count("data:") == 2


def test_reassembly_is_blocked():
    out = clean_fragment("<scr<script>ipt>alert(1)</scr</script>ipt><im<meta>g onerror=x>")
    low = out.lower()
    # Leftover words are escaped text, not tags.
    assert "<script" not in low
    assert "<meta" not in low
    assert "<img" not in low


def test_attribute_glued_to_a_quoted_value_is_still_handled():
    out = clean_fragment('<a title="x"href=//e>a</a><p title="x"onclick=y>b</p>')
    assert "href" not in out.lower()
    assert "onclick" not in out.lower()
    assert 'title="x"' in out
    assert "<a" in out and "<p" in out


def test_more_url_attributes_are_dropped():
    out = clean_fragment('<img srcset="a.png 1x"><video poster=//e></video><a ping=//e>p</a><td background=//e>')
    low = out.lower()
    for attr in ("srcset", "poster", "ping", "background", "//e"):
        assert attr not in low


def test_data_urls_are_only_allowed_as_images_in_src():
    out = clean_fragment('<a href="data:text/html,x">a</a><img src="data:text/html,x"><a href="data:image/png,x">b</a>')
    low = out.lower()
    assert "data:" not in low
    assert "href" not in low
    assert "src" not in low


def test_inline_svg_survives():
    svg = '<svg viewBox="0 0 10 10" width="20"><path d="M0 0L10 10" fill="none" stroke="#000"></path></svg>'
    out = clean_fragment(svg)
    assert out == svg


def test_style_element_and_attribute_survive():
    out = clean_fragment('<style>.a > p { color: red }</style><p style="color: red">x</p>')
    assert ".a > p { color: red }" in out
    assert 'style="color: red"' in out


def test_dropped_elements_lose_their_contents_but_unwrapped_ones_keep_them():
    out = clean_fragment("<noscript><p>n</p></noscript><template>t</template><form><b>kept</b></form>")
    assert "n</p>" not in out and "t</template>" not in out
    assert "<b>kept</b>" in out


def test_comments_are_removed_and_text_is_escaped():
    out = clean_fragment("<!-- c --><p>a < b & c</p>")
    assert "<!--" not in out
    assert "a &lt; b &amp; c" in out


def test_unclosed_elements_do_not_leak_into_the_page():
    out = clean_fragment("<div><span>x")
    assert out.count("<div>") == out.count("</div>") == 1
    assert out.count("<span>") == out.count("</span>") == 1


def test_slash_separated_handler_is_stripped():
    out = clean_fragment("<img/onerror=x><svg/onload=y>")
    assert "onerror" not in out.lower()
    assert "onload" not in out.lower()
    assert "<img" in out


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


def test_approach_slug():
    assert approach_slug("Footer band", 1) == "footer-band"
    assert approach_slug("  CTA: in the *hero*!  ", 2) == "cta-in-the-hero"
    assert approach_slug("Ünïcode café", 1) == "n-code-caf"
    assert approach_slug("x" * 39 + " yz", 1) == "x" * 39  # capped at 40, no trailing hyphen
    assert len(approach_slug("a" * 80, 1)) == 40
    assert approach_slug("???", 3) == "approach-3"
    # A slug that could be read as a page copy or another chat's file falls back too.
    assert approach_slug("V2", 1) == "approach-1"
    assert approach_slug("Layout v3", 2) == "approach-2"
    assert approach_slug("2 columns", 1) == "approach-1"


def test_clip_mockup():
    from phil.chat.mockup_page import CLIPPED, clip_mockup
    from phil.contracts.design import MOCKUP_MAX_CHARS

    assert CLIPPED == "\n…(mockup clipped)"
    assert clip_mockup("<p>short</p>") == "<p>short</p>"
    exact = "x" * MOCKUP_MAX_CHARS
    assert clip_mockup(exact) == exact
    lines = "\n".join(f"<p>line {i:05d}</p>" for i in range(3000))  # about 50,000 characters
    clipped = clip_mockup(lines)
    assert clipped.endswith(CLIPPED) and len(clipped) <= MOCKUP_MAX_CHARS + len(CLIPPED)
    assert clipped[: -len(CLIPPED)].endswith("</p>")  # cut at a line boundary
    one_line = clip_mockup("y" * 30_000)  # no line to cut at: a hard cut
    assert one_line == "y" * MOCKUP_MAX_CHARS + CLIPPED


def test_write_drawing_beside_the_page(tmp_path):
    path = write_drawing(tmp_path / "mockups", "chat1", "footer-band", "html", "<p>band</p>")
    assert path == tmp_path / "mockups" / "chat1-footer-band.html"
    assert path.read_text(encoding="utf-8") == "<p>band</p>"
    sketch = write_drawing(tmp_path / "mockups", "chat1", "footer-band", "sketch", "+--+")
    assert sketch.name == "chat1-footer-band.txt"
    _, first = write_page(tmp_path / "mockups", "chat1", "page")
    assert first is True  # a drawing's file is not a copy of the page
