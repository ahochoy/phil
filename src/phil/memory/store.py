"""The memory file on disk (spec 2026-10-15 §2): reads see the file as it is, and a change is applied
to the text on disk under a lock, then written atomically, so a second Phil or an edit by the user
between a read and a write is never lost."""

import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from phil import platform
from phil.memory.doc import MemoryDoc
from phil.store.paths import ProjectPaths

T = TypeVar("T")


class MemoryWriteError(Exception):
    pass


def _load(paths: ProjectPaths) -> MemoryDoc:
    try:
        text = paths.memory_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return MemoryDoc.new(paths.slug)
    return MemoryDoc.parse(text)


def read_memory(paths: ProjectPaths) -> MemoryDoc:
    """The memory file without a lock: the template when it's missing. An unreadable file raises OSError."""
    return _load(paths)


def write_atomic(path: Path, data: bytes) -> None:
    """Replace `path` with `data` through a fresh, uniquely named temp file in the same directory (never a
    fixed name, which a planted symlink could redirect). Keeps the file's mode, else 0o644. Raises OSError."""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".phil.tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on `<path>.lock` for a whole read-modify-write. The lock file always
    holds a byte, because msvcrt locks a byte range."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a+b") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            try:
                handle.write(b"\0")
                handle.flush()
            except OSError:  # another writer got there first and holds the byte locked
                pass
        platform.lock_file(handle)
        try:
            yield
        finally:
            platform.unlock_file(handle)


def update_memory(paths: ProjectPaths, change: Callable[[MemoryDoc], T]) -> T:
    """Apply `change` to the file as it is on disk now, under a lock, and write it back atomically
    when `change` altered it. Returns whatever `change` returns. Raises MemoryWriteError when the
    file can't be locked, read or written."""
    path = paths.memory_path
    try:
        with _locked(path):
            doc = _load(paths)
            before = doc.render()
            result = change(doc)
            after = doc.render()
            if after != before:
                write_atomic(path, after.encode("utf-8"))
            return result
    except OSError as exc:
        raise MemoryWriteError(f"can't write {path}: {exc}") from exc
