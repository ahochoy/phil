from phil.ui.fragments import to_text


def test_to_text_maps_classes_to_theme_styles():
    text = to_text([("class:phil.warn", "a"), ("", "b"), ("bold", "c")])
    assert text.plain == "abc"
    assert [str(s.style) for s in text.spans] == ["phil.warn", "bold"]


def test_to_text_keeps_newlines():
    assert to_text([("class:phil.id", "a"), ("", "\n"), ("", "b")]).plain == "a\nb"
