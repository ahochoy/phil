"""`BlockConsole`: one blank line before each block, never two, none at the top."""

import io

from rich.console import Console

from phil.ui.blocks import BlockConsole


def _console() -> tuple[BlockConsole, io.StringIO]:
    out = io.StringIO()
    return BlockConsole(Console(file=out, width=80, color_system=None)), out


def test_each_print_is_a_block_with_one_blank_between_and_none_on_top():
    console, out = _console()
    console.print("a")
    console.print("b")
    assert out.getvalue() == "a\n\nb\n"


def test_group_prints_one_block():
    console, out = _console()
    console.print("x")
    with console.group():
        console.print("a")
        console.print("b")
    assert out.getvalue() == "x\n\na\nb\n"


def test_an_empty_group_prints_nothing():
    console, out = _console()
    console.print("x")
    with console.group():
        pass
    console.print("y")
    assert out.getvalue() == "x\n\ny\n"


def test_continued_lines_join_the_block_above():
    console, out = _console()
    console.print("band")
    with console.continued():
        console.print("gate")
    assert out.getvalue() == "band\ngate\n"


def test_blank_never_doubles():
    console, out = _console()
    console.print("a")
    console.blank()
    console.blank()
    console.print("b")
    assert out.getvalue() == "a\n\nb\n"


def test_no_blank_at_the_very_top():
    console, out = _console()
    console.blank()
    console.print("a")
    assert out.getvalue() == "a\n"


def test_a_blank_printed_elsewhere_is_not_doubled():
    console, out = _console()
    console.print("a")
    out.write("\n")  # e.g. a prompt's own spacing
    console.mark_blank()
    console.print("b")
    assert out.getvalue() == "a\n\nb\n"


def test_a_line_printed_elsewhere_gets_its_blank_before_the_next_block():
    console, out = _console()
    out.write("you › hello\n")  # the user's typed line, which the console never printed
    console.mark_line()
    console.print("reply")
    assert out.getvalue() == "you › hello\n\nreply\n"


def test_an_empty_print_is_a_blank_line_not_two():
    console, out = _console()
    console.print("a")
    console.print()
    console.print("b")
    assert out.getvalue() == "a\n\nb\n"


def test_other_console_attributes_pass_through():
    console, _ = _console()
    assert console.width == 80
