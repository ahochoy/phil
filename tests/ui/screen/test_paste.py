"""A dropped image in the full-screen frame: the terminal pastes its path, and the box shows a chip
(spec 2026-11-19 images, "Attaching an image")."""

from textual import events

from phil.ui.screen.io import ScreenIO
from phil.ui.screen.widgets import Conversation, Item, PromptBox
from tests.ui.screen.pilot import pilot_run, until

PNG = b"\x89PNG\r\n\x1a\n"


def _io():
    io = ScreenIO()
    return io, io.chat_io(lambda *args: None)


def _box(io) -> PromptBox:
    return io.app.query_one(PromptBox)


def _conversation_text(io) -> str:
    return "\n".join(item.renderable.plain for item in io.app.query_one(Conversation).query(Item))


def test_paste_of_an_image_path_shows_a_chip_and_submits_the_path(tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(PNG)
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        await pilot.press(*"fix ")
        io.app.post_message(events.Paste(str(image)))
        await until(pilot, lambda: _box(io).text == "fix [image 1: a.png]")
        await pilot.press("enter")
        await until(pilot, lambda: got)
        assert got == [f"fix {image}"]
        await until(pilot, lambda: _conversation_text(io) == "you › fix [image 1: a.png]")
        assert type(got[0]) is str

    pilot_run(io.app, script)


def test_a_second_paste_numbers_its_chip_after_the_first(tmp_path):
    for name in ("a.png", "b.png"):
        (tmp_path / name).write_bytes(PNG)
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        io.app.post_message(events.Paste(f"{tmp_path / 'a.png'} "))
        await until(pilot, lambda: _box(io).text == "[image 1: a.png] ")
        io.app.post_message(events.Paste(str(tmp_path / "b.png")))
        await until(pilot, lambda: _box(io).text == "[image 1: a.png] [image 2: b.png]")
        await pilot.press("enter")
        await until(pilot, lambda: got)
        assert got == [f"{tmp_path / 'a.png'} {tmp_path / 'b.png'}"]

    pilot_run(io.app, script)


def test_a_typed_path_is_chipped_in_the_echo_only(tmp_path):
    """A path that arrived as keys (no paste event) stays in the box, and the echo still shows a chip."""
    image = tmp_path / "a.png"
    image.write_bytes(PNG)
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        _box(io).insert(f"see {image}")
        await pilot.press("enter")
        await until(pilot, lambda: got)
        assert got == [f"see {image}"]
        await until(pilot, lambda: _conversation_text(io) == "you › see [image 1: a.png]")

    pilot_run(io.app, script)


def test_a_path_pasted_straight_after_text_is_sent_apart_from_it(tmp_path):
    """Typed `fix`, then a drop with no space: the chip shows, and the sent text still names the image."""
    from phil.chat.attachments import attach, find_images

    image = tmp_path / "a.png"
    image.write_bytes(PNG)
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        await pilot.press(*"fix")
        io.app.post_message(events.Paste(str(image)))
        await until(pilot, lambda: _box(io).text == "fix[image 1: a.png]")
        await pilot.press(*"now")
        await pilot.press("enter")
        await until(pilot, lambda: got)
        assert got == [f"fix {image} now"]
        assert [found.path for found in find_images(got[0])] == [image]
        assert attach(got[0], tmp_path / "out", first_number=1).text == "fix [image 1: a.png] now"

    pilot_run(io.app, script)


def test_replacing_the_box_text_forgets_its_chips(tmp_path):
    for name in ("a.png", "b.png"):
        (tmp_path / name).write_bytes(PNG)
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        io.app.post_message(events.Paste(str(tmp_path / "a.png")))
        await until(pilot, lambda: _box(io).text == "[image 1: a.png]")
        io.prefill("retry ")
        await until(pilot, lambda: _box(io).text == "retry ")
        io.app.post_message(events.Paste(str(tmp_path / "b.png")))
        await until(pilot, lambda: _box(io).text == "retry [image 1: b.png]")
        await pilot.press("enter")
        await until(pilot, lambda: got)
        assert got == [f"retry {tmp_path / 'b.png'}"]

    pilot_run(io.app, script)


def test_a_deleted_chip_is_not_counted_or_expanded(tmp_path):
    for name in ("a.png", "b.png"):
        (tmp_path / name).write_bytes(PNG)
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        io.app.post_message(events.Paste(str(tmp_path / "a.png")))
        await until(pilot, lambda: _box(io).text == "[image 1: a.png]")
        _box(io).clear()  # the user deleted it
        await until(pilot, lambda: _box(io).text == "")
        io.app.post_message(events.Paste(str(tmp_path / "b.png")))
        await until(pilot, lambda: _box(io).text == "[image 1: b.png]")
        await pilot.press("enter")
        await until(pilot, lambda: got)
        assert got == [str(tmp_path / "b.png")]

    pilot_run(io.app, script)


def test_a_paste_without_images_is_unchanged(tmp_path):
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        io.app.post_message(events.Paste(f"{tmp_path}/missing.png"))
        await until(pilot, lambda: _box(io).text == f"{tmp_path}/missing.png")
        await pilot.press("enter")
        await until(pilot, lambda: got)
        assert got == [f"{tmp_path}/missing.png"]
        await until(pilot, lambda: _conversation_text(io) == f"you › {tmp_path}/missing.png")

    pilot_run(io.app, script)
