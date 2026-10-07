"""Static audit: no locale-default text I/O anywhere under `src/phil`.

Walks every `src/phil/**/*.py` file with `ast` and flags:
- a text-mode `open(...)`, `os.fdopen(...)`, `io.open(...)`, `codecs.open(...)`, or
  `Path.open(...)` with no `encoding=`;
- a `.read_text(...)`/`.write_text(...)` call with no `encoding=` (always text);
- a `tempfile.NamedTemporaryFile`/`TemporaryFile`/`SpooledTemporaryFile` call given an
  explicit text mode (their default mode is binary, `"w+b"`, so a call with no mode at all
  is fine) with no `encoding=`;
- a `subprocess.run`/`check_output`/`Popen` call with `text=True` or
  `universal_newlines=True` but no `encoding=`.

A mode argument that isn't a string literal is reported too, so a human looks at it.

`_check_source` is the audit itself, over a source string; `test_no_locale_default_text_io_in_src`
runs it over every file in `src/phil`, and the `test_audit_catches_*` tests below run it over
small snippets to prove the gate actually catches each pattern.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "phil"

# Keyword arguments `Path.open()` itself accepts. A bound `.open(...)` call outside this
# shape (e.g. `tarfile.open(fileobj=..., mode="r:")`, or `ChatSession.open(paths, chat_id)`,
# which takes two unrelated positional arguments) isn't a file open at all, and is skipped.
_PATH_OPEN_KWARGS = {"mode", "buffering", "encoding", "errors", "newline"}
_MODULE_OPEN_NAMES = {"io", "codecs"}  # `io.open`/`codecs.open`: free-function style, like `open`
_TEMPFILE_NAMES = {"NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}
_SUBPROCESS_NAMES = {"run", "check_output", "Popen"}
_TEXT_ALWAYS_ATTRS = {"read_text", "write_text"}


def _mode_node(call: ast.Call, index: int) -> ast.expr | None:
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == "mode":
            return kw.value
    return None


def _has_encoding(call: ast.Call) -> bool:
    return any(kw.arg == "encoding" for kw in call.keywords)


def _is_binary_mode(mode_node: ast.expr | None) -> bool | None:
    """True if the mode is a binary ("b") literal; False if it's a text literal (or absent,
    since the default mode is text); None if it isn't a string literal at all."""
    if mode_node is None:
        return False
    if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
        return "b" in mode_node.value
    return None


def _check_source(source: str, label: str) -> list[str]:
    tree = ast.parse(source, filename=label)
    offenses: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        line = node.lineno

        # `Path.read_text()` takes no positional argument and `Path.write_text()` takes exactly
        # one ("data"); a call with more (e.g. `ArtifactStore.write_text(relative, text)`, a
        # same-named method with its own signature) isn't `Path`'s and is out of scope here.
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _TEXT_ALWAYS_ATTRS
            and len(node.args) <= 1
            and not _has_encoding(node)
        ):
            offenses.append(f"{label}:{line}: .{func.attr}(...) with no encoding=")

        is_free_open = isinstance(func, ast.Name) and func.id == "open"
        is_fdopen = isinstance(func, ast.Attribute) and func.attr == "fdopen"
        # `io.open(path, "w")` / `codecs.open(path, "w")`: free-function style like `open`
        # itself (mode is the 2nd argument), not `Path.open`'s bound style (mode is the 1st).
        is_module_open = (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and isinstance(func.value, ast.Name)
            and func.value.id in _MODULE_OPEN_NAMES
        )
        is_bound_open = (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and not is_module_open
            and len(node.args) <= 1
            and all(kw.arg in _PATH_OPEN_KWARGS for kw in node.keywords)
        )
        if is_free_open or is_fdopen or is_module_open or is_bound_open:
            mode_index = 0 if is_bound_open else 1
            mode_node = _mode_node(node, mode_index)
            binary = _is_binary_mode(mode_node)
            if binary is None:
                offenses.append(f"{label}:{line}: open() mode isn't a string literal — review by hand")
            elif not binary and not _has_encoding(node):
                offenses.append(f"{label}:{line}: text-mode open() with no encoding=")

        is_tempfile = (isinstance(func, ast.Attribute) and func.attr in _TEMPFILE_NAMES) or (
            isinstance(func, ast.Name) and func.id in _TEMPFILE_NAMES
        )
        if is_tempfile:
            mode_node = _mode_node(node, 0)
            if mode_node is not None:  # no mode at all means the (binary) default; that's fine
                binary = _is_binary_mode(mode_node)
                if binary is None:
                    offenses.append(f"{label}:{line}: tempfile mode isn't a string literal — review by hand")
                elif not binary and not _has_encoding(node):
                    offenses.append(f"{label}:{line}: text-mode tempfile with no encoding=")

        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name in _SUBPROCESS_NAMES:
            wants_text = any(
                kw.arg in ("text", "universal_newlines")
                and isinstance(kw.value, ast.Constant)
                and kw.value.value
                for kw in node.keywords
            )
            if wants_text and not _has_encoding(node):
                offenses.append(f"{label}:{line}: subprocess {name}(...) with text=True but no encoding=")
    return offenses


def _offenses(path: Path) -> list[str]:
    rel = path.relative_to(SRC.parent.parent)
    return _check_source(path.read_text(encoding="utf-8"), str(rel))


def test_no_locale_default_text_io_in_src():
    offenses: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        offenses += _offenses(path)
    assert offenses == [], "locale-default text I/O found:\n" + "\n".join(offenses)


@pytest.mark.parametrize(
    "source",
    [
        'io.open(p, "w")',
        'io.open(p, "a")',
        'codecs.open(p, "r")',
        'codecs.open(p, "w")',
    ],
)
def test_audit_catches_io_and_codecs_open_in_text_mode(source):
    assert _check_source(source, "snippet") != []


@pytest.mark.parametrize(
    "source",
    [
        'io.open(p, "w", encoding="utf-8")',
        'codecs.open(p, "r", encoding="utf-8")',
        'io.open(p, "rb")',  # binary: no encoding needed
        'codecs.open(p, "wb")',
    ],
)
def test_audit_passes_encoded_or_binary_io_and_codecs_open(source):
    assert _check_source(source, "snippet") == []


@pytest.mark.parametrize(
    "source",
    [
        'tempfile.NamedTemporaryFile("w")',
        'tempfile.TemporaryFile(mode="w")',
        'tempfile.SpooledTemporaryFile("a")',
        'NamedTemporaryFile("w")',  # imported directly (`from tempfile import NamedTemporaryFile`)
    ],
)
def test_audit_catches_tempfile_text_mode(source):
    assert _check_source(source, "snippet") != []


@pytest.mark.parametrize(
    "source",
    [
        "tempfile.NamedTemporaryFile()",  # no mode at all: binary default, fine
        'tempfile.NamedTemporaryFile("wb")',
        'tempfile.NamedTemporaryFile("w", encoding="utf-8")',
        'tempfile.TemporaryFile(mode="w", encoding="utf-8")',
        'tempfile.SpooledTemporaryFile(mode="rb")',
    ],
)
def test_audit_passes_binary_or_encoded_tempfile(source):
    assert _check_source(source, "snippet") == []
