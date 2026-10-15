"""The memory file on disk (spec 2026-10-15 §2): reads see the file as it is, and a change is applied
to the text on disk under a lock, then written atomically, so a second Phil or an edit by the user
between a read and a write is never lost."""

import os
import secrets
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


UMASK_MODE = None  # `new_mode` for a file of the user's own: 0o666 less their umask, as the OS applies it


def _umask_temp(path: Path) -> tuple[int, str]:
    """A fresh, uniquely named temp file created with 0o666, which the OS reduces by the umask (reading
    the umask would mean changing it, which races with other threads)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    for _ in range(100):
        name = str(path.parent / f".{path.name}.{secrets.token_hex(6)}.phil.tmp")
        try:
            return os.open(name, flags, 0o666), name
        except FileExistsError:
            continue
    raise FileExistsError(f"no free temp name next to {path}")


def write_atomic(path: Path, data: bytes, *, new_mode: int | None = 0o600) -> None:
    """Replace `path` with `data` through a fresh, uniquely named temp file in the same directory (never a
    fixed name, which a planted symlink could redirect). An existing file keeps its mode; a new one gets
    `new_mode` (0o600 for Phil's own files; UMASK_MODE for 0o666 less the umask). Raises OSError."""
    try:
        mode: int | None = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        mode = new_mode
    if mode is None:
        fd, tmp_name = _umask_temp(path)
    else:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".phil.tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if mode is not None:
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
            try:
                # Strict, unlike read_memory: replacing bad bytes and writing back would change the user's file.
                doc = MemoryDoc.parse(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                doc = MemoryDoc.new(paths.slug)
            except UnicodeDecodeError as exc:
                raise MemoryWriteError("memory.md isn't valid UTF-8; not updated") from exc
            before = doc.render()
            result = change(doc)
            after = doc.render()
            if after != before:
                write_atomic(path, after.encode("utf-8"))
            return result
    except OSError as exc:
        raise MemoryWriteError(f"can't write {path}: {exc}") from exc
