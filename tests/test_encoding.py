"""Static audit: no locale-default text I/O anywhere under `src/phil`.

Walks every `src/phil/**/*.py` file with `ast` and flags:
- a text-mode `open(...)`, `os.fdopen(...)`, or `Path.open(...)` with no `encoding=`;
- a `.read_text(...)`/`.write_text(...)` call with no `encoding=` (always text);
- a `subprocess.run`/`check_output`/`Popen` call with `text=True` or
  `universal_newlines=True` but no `encoding=`.

A mode argument that isn't a string literal is reported too, so a human looks at it.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "phil"

# Keyword arguments `Path.open()` itself accepts. A bound `.open(...)` call outside this
# shape (e.g. `tarfile.open(fileobj=..., mode="r:")`, or `ChatSession.open(paths, chat_id)`,
# which takes two unrelated positional arguments) isn't a file open at all, and is skipped.
_PATH_OPEN_KWARGS = {"mode", "buffering", "encoding", "errors", "newline"}
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


def _offenses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenses: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        line = node.lineno
        rel = path.relative_to(SRC.parent.parent)

        # `Path.read_text()` takes no positional argument and `Path.write_text()` takes exactly
        # one ("data"); a call with more (e.g. `ArtifactStore.write_text(relative, text)`, a
        # same-named method with its own signature) isn't `Path`'s and is out of scope here.
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _TEXT_ALWAYS_ATTRS
            and len(node.args) <= 1
            and not _has_encoding(node)
        ):
            offenses.append(f"{rel}:{line}: .{func.attr}(...) with no encoding=")

        is_free_open = isinstance(func, ast.Name) and func.id == "open"
        is_fdopen = isinstance(func, ast.Attribute) and func.attr == "fdopen"
        is_bound_open = (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and len(node.args) <= 1
            and all(kw.arg in _PATH_OPEN_KWARGS for kw in node.keywords)
        )
        if is_free_open or is_fdopen or is_bound_open:
            mode_index = 1 if (is_free_open or is_fdopen) else 0
            mode_node = _mode_node(node, mode_index)
            binary = _is_binary_mode(mode_node)
            if binary is None:
                offenses.append(f"{rel}:{line}: open() mode isn't a string literal — review by hand")
            elif not binary and not _has_encoding(node):
                offenses.append(f"{rel}:{line}: text-mode open() with no encoding=")

        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name in _SUBPROCESS_NAMES:
            wants_text = any(
                kw.arg in ("text", "universal_newlines")
                and isinstance(kw.value, ast.Constant)
                and kw.value.value
                for kw in node.keywords
            )
            if wants_text and not _has_encoding(node):
                offenses.append(f"{rel}:{line}: subprocess {name}(...) with text=True but no encoding=")
    return offenses


def test_no_locale_default_text_io_in_src():
    offenses: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        offenses += _offenses(path)
    assert offenses == [], "locale-default text I/O found:\n" + "\n".join(offenses)
