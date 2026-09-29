from phil.contracts import Brief, Decision, Ref
from phil.ui.brief_view import render_brief
from phil.ui.theme import make_console


def test_render_brief():
    console = make_console(record=True, width=120)
    render_brief(console, Brief(
        headline="Auth is in [bold]auth.py[/bold]", status="read 3 files", points=["login()", "logout()"],
        needs_you=[Decision(question="Split it?", options=["yes", "no"])], details=[Ref(label="auth", path="src/auth.py")],
    ))
    text = console.export_text()
    assert "Auth is in [bold]auth.py[/bold]" in text
    assert "• login()" in text and "? Split it? [yes / no]" in text and "→ auth: src/auth.py" in text
