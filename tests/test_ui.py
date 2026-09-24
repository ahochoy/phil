from phil.ui.theme import STYLE_NAMES, make_console


def test_all_styles_are_namespaced():
    assert STYLE_NAMES
    assert all(name.startswith("phil.") for name in STYLE_NAMES)


def test_console_renders_theme_styles():
    console = make_console(record=True, width=80, force_terminal=False)
    for name in STYLE_NAMES:
        console.print(f"[{name}]sample[/]")
    assert console.export_text().count("sample") == len(STYLE_NAMES)
