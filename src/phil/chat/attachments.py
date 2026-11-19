"""Finding images in a message and attaching them.

A terminal pastes a dropped file as its path. This module finds those paths in a message, replaces
each with a short `[image N: name]` chip, and copies the files into the chat's folder so they
outlive the original. It is pure: it reads the pasted paths and writes only the copies.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from rich.cells import cell_len

from phil.ui.bursts import one_line

IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
MAX_IMAGES = 5
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_CHIP_CELLS = 40
_TRAILING = ".,;:!?)]}"
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}

# macOS puts U+202F (narrow no-break space) in screenshot names, and the file on disk keeps it, so
# a path run must hold it (and the no-break space) as an ordinary character, never as a separator.
_SPACES = "  "
_RUN = rf"(?:\\[ {_SPACES}]|[{_SPACES}]|[^\s'\"])"
_TOKEN = re.compile(
    rf"""(?<!\S)(?:
        "(?P<dq>(?:/|~/|file://)[^"\n]*)"
      | '(?P<sq>(?:/|~/|file://)[^'\n]*)'
      | (?P<uri>file://{_RUN}+)
      | (?P<bare>(?:/|~/){_RUN}*)
    )""",
    re.VERBOSE,
)
_ESCAPED_SPACE = re.compile(rf"\\([ {_SPACES}])")


@dataclass(frozen=True)
class Found:
    """An image path in a message: `raw` is the exact text pasted, `path` the existing file."""

    raw: str
    path: Path


@dataclass(frozen=True)
class Attached:
    """A message with its images swapped for chips: `images` are the copies, `skipped` the reasons."""

    text: str
    images: tuple[Path, ...]
    skipped: tuple[str, ...]


def mime_type(path: Path) -> str:
    return _MIME[path.suffix.lower()]


def chip(index: int, name: str) -> str:
    """`[image N: name]`, the name cut with `…` so what's inside the brackets fits 40 cells."""
    label = f"image {index}: "
    name = one_line(name)
    budget = _CHIP_CELLS - cell_len(label)
    if cell_len(name) > budget:
        kept = ""
        for char in name:
            if cell_len(kept + char) + 1 > budget:
                break
            kept += char
        name = kept + "…"
    return f"[{label}{name}]"


def _image_at(candidate: str) -> Path | None:
    """The absolute path of `candidate` when it names an existing, regular, non-symlink image."""
    try:
        if candidate.startswith("file://"):
            candidate = unquote(candidate.removeprefix("file://").removeprefix("localhost"))
        else:
            candidate = _ESCAPED_SPACE.sub(r"\1", candidate)
        if not (candidate.startswith("/") or candidate.startswith("~/")):
            return None
        path = Path(candidate).expanduser()
        if not path.is_absolute() or path.suffix.lower() not in IMAGE_EXTS:
            return None
        if path.is_symlink() or not path.is_file():
            return None
        return Path(os.path.abspath(path))
    except (OSError, ValueError, RuntimeError):
        return None


def _scan(text: str) -> list[tuple[int, int, Found]]:
    """Every image path in `text` as (start, end, Found), in text order."""
    out: list[tuple[int, int, Found]] = []
    for match in _TOKEN.finditer(text):
        quoted = match.group("dq") or match.group("sq")
        if quoted is not None:
            raw, candidate = match.group(0), quoted
        else:
            raw = candidate = match.group(0)
        path = _image_at(candidate)
        if path is None and quoted is None:
            stripped = candidate.rstrip(_TRAILING)
            if stripped != candidate:
                path = _image_at(stripped)
                raw = stripped
        if path is not None:
            out.append((match.start(), match.start() + len(raw), Found(raw=raw, path=path)))
    return out


def find_images(text: str) -> list[Found]:
    """The existing image files pasted in `text` (absolute or `~/` paths, escaped, quoted or as
    `file://` URIs), in text order, each file once."""
    seen: set[Path] = set()
    found: list[Found] = []
    for _start, _end, item in _scan(text):
        if item.path not in seen:
            seen.add(item.path)
            found.append(item)
    return found


def attach(text: str, images_dir: Path, *, first_number: int) -> Attached:
    """`text` with each pasted image replaced by a chip (numbered from 1), and each copied to
    `images_dir/<n>-<name>` with `n` counting up from `first_number`. An image that is over the
    limits or can't be copied is skipped with a reason, and its path stays as text."""
    pieces: list[str] = []
    copies: list[Path] = []
    skipped: list[str] = []
    chips: dict[Path, str] = {}
    refused: set[Path] = set()
    cursor = 0
    for start, end, item in _scan(text):
        path = item.path
        replacement = chips.get(path)
        if replacement is None and path not in refused:
            replacement = _copy(path, images_dir, first_number + len(copies), len(copies), skipped, copies)
            if replacement is None:
                refused.add(path)
            else:
                chips[path] = replacement
        if replacement is not None:
            pieces.append(text[cursor:start])
            pieces.append(replacement)
            cursor = end
    pieces.append(text[cursor:])
    return Attached(text="".join(pieces), images=tuple(copies), skipped=tuple(skipped))


def _copy(path: Path, images_dir: Path, number: int, attached: int, skipped: list[str], copies: list[Path]) -> str | None:
    """Copy one image and return its chip, or add a skip reason and return None."""
    name = one_line(path.name)
    if attached >= MAX_IMAGES:
        skipped.append(f"Skipped {name}: only {MAX_IMAGES} images per message")
        return None
    try:
        if path.stat().st_size > MAX_IMAGE_BYTES:
            skipped.append(f"Skipped {name}: over {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
            return None
        images_dir.mkdir(parents=True, exist_ok=True)
        target = images_dir / f"{number}-{path.name}"
        shutil.copyfile(path, target)
    except OSError:
        skipped.append(f"Skipped {name}: couldn't copy it")
        return None
    copies.append(target)
    return chip(len(copies), path.name)
