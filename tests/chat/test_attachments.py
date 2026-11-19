"""Finding image paths in a message and attaching them as chips."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.cells import cell_len

from phil.chat.attachments import (
    IMAGE_EXTS,
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    Attached,
    Found,
    attach,
    chip,
    find_images,
    mime_type,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def _png(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(PNG)
    return path


def test_constants():
    assert IMAGE_EXTS == frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
    assert MAX_IMAGES == 5
    assert MAX_IMAGE_BYTES == 5 * 1024 * 1024  # Anthropic's API refuses larger images


def test_bare_and_tilde_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    bare = _png(tmp_path, "a.png")
    _png(tmp_path, "b.JPG")
    found = find_images(f"one {bare} two ~/b.JPG three")
    assert found == [
        Found(raw=str(bare), path=bare),
        Found(raw="~/b.JPG", path=tmp_path / "b.JPG"),
    ]


def test_relative_paths_are_ignored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _png(tmp_path, "a.png")
    assert find_images("see a.png and ./a.png") == []


def test_backslash_escaped_macos_screenshot(tmp_path):
    name = "Screenshot 2026-11-19 at 9.07.45 AM.png"
    target = _png(tmp_path, name)
    escaped = str(target).replace(" ", "\\ ")
    assert " " in escaped
    found = find_images(f"look at {escaped} please")
    assert found == [Found(raw=escaped, path=target)]
    assert found[0].path.exists()


def test_u202f_is_never_normalised(tmp_path):
    with_narrow = _png(tmp_path, "shot 9.07 AM.png")
    plain = tmp_path / "shot 9.07 AM.png"
    assert not plain.exists()
    found = find_images(str(with_narrow).replace(" ", "\\ "))
    assert [f.path for f in found] == [with_narrow]


def test_quoted_and_file_uri(tmp_path):
    spaced = _png(tmp_path, "a b.png")
    single = f"'{spaced}'"
    double = f'"{spaced}"'
    uri = "file://" + str(spaced).replace(" ", "%20")
    for text, raw in ((single, single), (double, double), (uri, uri)):
        assert find_images(f"x {text} y") == [Found(raw=raw, path=spaced)]


def test_non_images_and_missing_stay_text(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "notes.txt").write_text("hi")
    assert find_images("~/notes.txt and /tmp/gone.png") == []


def test_urls_are_not_paths(tmp_path):
    _png(tmp_path, "a.png")
    assert find_images(f"https://example.com{tmp_path}/a.png") == []


def test_trailing_punctuation_is_not_part_of_the_path(tmp_path):
    target = _png(tmp_path, "a.png")
    assert find_images(f"look at {target}.") == [Found(raw=str(target), path=target)]


def test_duplicates_are_found_once(tmp_path):
    target = _png(tmp_path, "a.png")
    assert len(find_images(f"{target} and again {target}")) == 1


def test_attach_replaces_with_chips_and_copies(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    a, b = _png(src, "a.png"), _png(src, "b.png")
    images_dir = tmp_path / "chat" / "images"
    result = attach(f"see {a} and {b}", images_dir, first_number=3)
    assert isinstance(result, Attached)
    assert result.text == "see [image 1: a.png] and [image 2: b.png]"
    assert [p.name for p in result.images] == ["3-a.png", "4-b.png"]
    assert all(p.parent == images_dir and p.read_bytes() == PNG for p in result.images)
    assert result.skipped == ()


def test_same_image_twice_shares_a_chip(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    a = _png(src, "a.png")
    result = attach(f"{a} and {a}", tmp_path / "images", first_number=1)
    assert result.text == "[image 1: a.png] and [image 1: a.png]"
    assert len(result.images) == 1


def test_text_without_images_is_unchanged(tmp_path):
    result = attach("nothing here", tmp_path / "images", first_number=1)
    assert result == Attached(text="nothing here", images=(), skipped=())
    assert not (tmp_path / "images").exists()


def test_limits(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    files = [_png(src, f"{n}.png") for n in "abcde"] + [_png(src, "f.png")]
    result = attach(" ".join(str(f) for f in files), tmp_path / "images", first_number=1)
    assert len(result.images) == 5
    assert result.skipped == ("Skipped f.png: only 5 images per message",)
    assert str(files[-1]) in result.text

    big = src / "big.png"
    with big.open("wb") as handle:
        handle.truncate(6 * 1024 * 1024)
    result = attach(f"look {big}", tmp_path / "images2", first_number=1)
    assert result.skipped == ("Skipped big.png: over 5 MB",)
    assert result.text == f"look {big}"
    assert result.images == ()

    edge = src / "edge.png"
    with edge.open("wb") as handle:
        handle.truncate(5 * 1024 * 1024)
    result = attach(f"look {edge}", tmp_path / "images3", first_number=1)
    assert result.skipped == () and len(result.images) == 1  # exactly 5 MB is allowed


def test_long_name_chip_is_cut():
    text = chip(1, "x" * 60 + ".png")
    inside = text[1:-1]
    assert text.startswith("[image 1: ") and text.endswith("…]")
    assert cell_len(inside) <= 40
    assert chip(2, "a.png") == "[image 2: a.png]"


def test_symlink_and_directory_rejected(tmp_path):
    real = _png(tmp_path, "real.png")
    link = tmp_path / "link.png"
    link.symlink_to(real)
    folder = tmp_path / "x.png"
    folder.mkdir()
    assert find_images(f"{link} {folder}") == []


def test_escapes_in_names_are_printable(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    odd = _png(src, "a\x1bb.png")
    result = attach(str(odd), tmp_path / "images", first_number=1)
    assert result.text == "[image 1: a?b.png]"


def test_copy_failure_is_skipped(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    a = _png(src, "a.png")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("phil.chat.attachments.shutil.copyfile", boom)
    result = attach(f"see {a}", tmp_path / "images", first_number=1)
    assert result.skipped == ("Skipped a.png: couldn't copy it",)
    assert result.text == f"see {a}"
    assert result.images == ()


@pytest.mark.parametrize(
    ("name", "mime"),
    [
        ("a.png", "image/png"),
        ("a.JPG", "image/jpeg"),
        ("a.jpeg", "image/jpeg"),
        ("a.webp", "image/webp"),
        ("a.gif", "image/gif"),
    ],
)
def test_mime_type(name, mime):
    assert mime_type(Path(name)) == mime


def test_preview_chips_as_attach_does_without_copying(tmp_path):
    from phil.chat.attachments import preview

    folder = tmp_path / "shots"
    folder.mkdir()
    a, b = _png(folder, "a.png"), _png(folder, "b.png")
    big = folder / "big.png"
    with open(big, "wb") as handle:
        handle.truncate(MAX_IMAGE_BYTES + 1)
    text = f"see {a} and {big} then {b} and {a} again"
    assert preview(text) == f"see [image 1: a.png] and {big} then [image 2: b.png] and [image 1: a.png] again"
    assert sorted(path.name for path in folder.iterdir()) == ["a.png", "b.png", "big.png"]  # nothing copied
    assert preview(text) == attach(text, tmp_path / "out", first_number=1).text
    assert preview("no images here") == "no images here"
