"""The mockup page: a plain frame around the model's HTML fragments.

Pure helpers, no controller wiring. The CSP is the real guard against script and
network access; `clean_fragment` only removes the obvious cases.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from phil.contracts.design import MOCKUP_MAX_CHARS

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "form-action 'none'; base-uri 'none'"
)

CLOSING_LINE = "Pick in Phil: Enter or a number to choose, `show N <note>` to redraw."

_TAG_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9:-]*$")
_ATTR_NAME = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:.-]*$")

# Dropped with everything inside them (when they are closed).
_DROP_WITH_CONTENTS = frozenset(
    {
        "script", "iframe", "object", "frameset", "frame", "noscript", "template", "applet",
        # SVG animation can rewrite href to a script URL at run time.
        "animate", "set", "animatemotion", "animatetransform",
    }
)  # fmt: skip
# Dropped as tags only; their children stay.
_DROP_TAG_ONLY = frozenset({"meta", "base", "link", "embed", "form"})
_VOID = frozenset(
    {
        "area", "base", "basefont", "br", "col", "embed", "frame", "hr", "img", "input",
        "keygen", "link", "meta", "param", "source", "track", "wbr",
    }
)  # fmt: skip
# The parser lowercases names; SVG's camelCase ones are restored on output.
_SVG_CASE = {
    name.lower(): name
    for name in (
        "viewBox preserveAspectRatio gradientUnits gradientTransform patternUnits patternTransform "
        "patternContentUnits clipPathUnits maskUnits maskContentUnits filterUnits primitiveUnits "
        "markerWidth markerHeight markerUnits refX refY stdDeviation textLength lengthAdjust "
        "startOffset baseFrequency numOctaves spreadMethod pathLength tableValues "
        "linearGradient radialGradient clipPath textPath feGaussianBlur feOffset feBlend feColorMatrix "
        "feComposite feFlood feMerge feMergeNode feMorphology feTurbulence feDropShadow"
    ).split()
}
_ALWAYS_DROPPED_ATTRS = frozenset({"ping", "srcset"})
# Only "#..." values are kept on these.
_LINK_ATTRS = frozenset({"href", "xlink:href", "action", "formaction", "cite", "codebase", "manifest"})
# Only "data:image/..." values are kept on these.
_IMAGE_ATTRS = frozenset({"src", "poster", "background", "data"})

_STYLE = """\
*, *::before, *::after { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px 48px; font: 16px/1.5 system-ui, sans-serif; color: #222; background: #fff; }
main { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
.comparing { color: #666; margin: 0 0 24px; }
.drawings { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr); gap: 24px; align-items: start; }
.drawing h2 { font-size: 1.1rem; margin: 0 0 8px; }
.canvas { border: 1px solid #bbb; border-radius: 6px; padding: 12px; background: #fafafa; overflow: auto; }
.closing { margin-top: 32px; color: #444; border-top: 1px solid #ddd; padding-top: 12px; }
@media (max-width: 800px) { .drawings { grid-auto-flow: row; } }

/* Wireframe building blocks the model can use in its fragments. */
.mock-nav { display: flex; align-items: center; gap: 12px; padding: 8px 12px; background: #e4e4e4; border: 1px solid #bbb; }
.mock-sidebar { width: 160px; flex: none; padding: 12px; background: #ececec; border: 1px solid #bbb; }
.mock-content { flex: 1; min-width: 0; padding: 12px; background: #f4f4f4; border: 1px solid #ccc; }
.mock-button { display: inline-block; padding: 6px 14px; background: #d0d0d0; border: 1px solid #999; border-radius: 4px; }
.mock-input { display: block; width: 100%; padding: 6px 10px; background: #fff; border: 1px solid #aaa; border-radius: 4px; color: #888; }
.placeholder { min-height: 48px; background: #ddd; border: 1px dashed #999; color: #777; padding: 8px; }
.split { display: flex; gap: 12px; }
.split > * { flex: 1; min-width: 0; }
"""


class _Node:
    __slots__ = ("name", "attrs", "closed")

    def __init__(self, name: str, attrs: list[tuple[str, str | None]]) -> None:
        self.name = name
        self.attrs = attrs
        self.closed = False  # True only when an end tag named it explicitly


def _keep_attr(name: str, value: str | None) -> bool:
    if not _ATTR_NAME.match(name) or name.startswith("on") or name in _ALWAYS_DROPPED_ATTRS:
        return False
    if name in _LINK_ATTRS:
        return (value or "").strip().startswith("#")
    if name in _IMAGE_ATTRS:
        return (value or "").strip().lower().startswith("data:image/")
    return True


class _Cleaner(HTMLParser):
    """Rebuilds a fragment from its parsed structure, dropping what is unsafe.

    Tokens are collected flat (open, close, text) and serialized afterwards, so deep
    nesting can't overflow the stack. Every element still open is closed, so one
    drawing can't swallow the next.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tokens: list[tuple[str, object]] = []
        self._stack: list[_Node] = []

    def set_cdata_mode(self, elem: str, *args: Any, **kwargs: Any) -> None:
        # Only <style> keeps raw-text handling. Everything else (script, iframe,
        # textarea...) is parsed as markup, so an unclosed one can't swallow the rest.
        if elem == "style":
            super().set_cdata_mode(elem, *args, **kwargs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not _TAG_NAME.match(tag):
            return
        node = _Node(tag, [(n, v) for n, v in attrs if _keep_attr(n, v)])
        self.tokens.append(("open", node))
        if tag not in _VOID:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].name == tag:
                while len(self._stack) > index + 1:
                    self.tokens.append(("close", self._stack.pop()))
                node = self._stack.pop()
                node.closed = True
                self.tokens.append(("close", node))
                return

    def handle_data(self, data: str) -> None:
        if self._stack and self._stack[-1].name == "style":
            # CSS needs `>` and quotes as written; `<` is escaped so it can't open a tag
            # if a browser parses this style element as SVG content.
            self.tokens.append(("raw", data.replace("<", "\\3c ")))
        else:
            self.tokens.append(("text", data))

    def handle_entityref(self, name: str) -> None:
        self.tokens.append(("raw", f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.tokens.append(("raw", f"&#{name};"))

    def finish(self) -> str:
        self.close()
        while self._stack:
            self.tokens.append(("close", self._stack.pop()))
        out: list[str] = []
        skipping: _Node | None = None
        for kind, value in self.tokens:
            if kind == "open":
                node = value
                assert isinstance(node, _Node)
                if skipping is not None:
                    continue
                if node.name in _DROP_WITH_CONTENTS and (node.closed or node.name in _VOID):
                    skipping = node
                elif node.name not in _DROP_WITH_CONTENTS and node.name not in _DROP_TAG_ONLY:
                    out.append(self._open_tag(node))
            elif kind == "close":
                node = value
                assert isinstance(node, _Node)
                if skipping is not None:
                    if skipping is node:
                        skipping = None
                    continue
                if node.name not in _DROP_WITH_CONTENTS and node.name not in _DROP_TAG_ONLY:
                    out.append(f"</{_SVG_CASE.get(node.name, node.name)}>")
            elif skipping is None:
                text = str(value)
                out.append(text if kind == "raw" else html.escape(text, quote=False))
        return "".join(out)

    @staticmethod
    def _open_tag(node: _Node) -> str:
        attrs = "".join(
            f" {_SVG_CASE.get(name, name)}"
            if value is None
            else f' {_SVG_CASE.get(name, name)}="{html.escape(value, quote=True)}"'
            for name, value in node.attrs
        )
        return f"<{_SVG_CASE.get(node.name, node.name)}{attrs}>"


def clean_fragment(fragment: str) -> str:
    """Re-serialize a fragment from its parsed structure, keeping only safe parts.

    Scripts, frames, objects and the like go with their contents; `meta`, `base`,
    `link`, `embed` and `form` go as tags only. Event handlers are dropped, and URL
    attributes keep only `#` links and `data:image/` sources. Comments are dropped,
    text and attribute values are re-escaped, and unclosed elements are closed.
    The CSP is still the real guard.
    """
    cleaner = _Cleaner()
    cleaner.feed(fragment)
    return cleaner.finish()


def comparing_line(count: int, objective: str) -> str:
    noun = "approach" if count == 1 else "approaches"
    return f"{count} {noun} to {objective}: compare placement and content."


def build_page(objective: str, comparing: str, drawings: list[tuple[int, str, str]]) -> str:
    esc = html.escape
    blocks = "\n".join(
        f'<section class="drawing">\n<h2>{number}. {esc(name)}</h2>\n'
        f'<div class="canvas">\n{fragment}\n</div>\n</section>'
        for number, name, fragment in drawings
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(objective)}</title>\n"
        f"<style>\n{_STYLE}</style>\n"
        "</head>\n<body>\n<main>\n"
        f"<h1>{esc(objective)}</h1>\n"
        f'<p class="comparing">{esc(comparing)}</p>\n'
        f'<div class="drawings">\n{blocks}\n</div>\n'
        f'<p class="closing">{esc(CLOSING_LINE)}</p>\n'
        "</main>\n</body>\n</html>\n"
    )


def browser_available(env: Mapping[str, str], platform: str) -> bool:
    if env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        return False
    if platform.startswith("linux"):
        return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    return True


def write_page(folder: Path, chat_id: str, page: str) -> tuple[Path, bool]:
    """Write `<chat_id>.html` and the next `<chat_id>-v<n>.html`; `first` means no copy existed."""
    folder.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(chat_id)}-v(\d+)\.html$")
    seen = [int(m.group(1)) for p in folder.iterdir() if (m := pattern.match(p.name))]
    current = folder / f"{chat_id}.html"
    current.write_text(page, encoding="utf-8")
    (folder / f"{chat_id}-v{max(seen, default=0) + 1}.html").write_text(page, encoding="utf-8")
    return current, not seen


CLIPPED = "\n…(mockup clipped)"


def clip_mockup(text: str) -> str:
    """The picked mockup as carried into planning and the run: at most MOCKUP_MAX_CHARS, cut at
    the last line break when one is in the second half, then marked as clipped."""
    if len(text) <= MOCKUP_MAX_CHARS:
        return text
    cut = text[:MOCKUP_MAX_CHARS]
    newline = cut.rfind("\n")
    if newline >= MOCKUP_MAX_CHARS // 2:
        cut = cut[:newline]
    return cut + CLIPPED


SLUG_MAX = 40
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
# A slug that would make `<chat id>-<slug>` read as a page copy (`-v<n>`) or as a file of another
# chat (chat ids end in `-<n>` when two open in the same second) is not used.
_AMBIGUOUS_SLUG = re.compile(r"^(?:\d.*|(?:.*-)?v\d+)$")


def approach_slug(name: str, index: int) -> str:
    """An approach's name for its drawing's file: lowercase alphanumerics and hyphens, at most 40
    characters; `approach-<index>` when nothing usable is left."""
    slug = _NOT_SLUG.sub("-", name.lower()).strip("-")[:SLUG_MAX].rstrip("-")
    if not slug or _AMBIGUOUS_SLUG.match(slug):
        return f"approach-{index}"
    return slug


def drawing_path(folder: Path, chat_id: str, slug: str, kind: str) -> Path:
    return folder / f"{chat_id}-{slug}.{'html' if kind == 'html' else 'txt'}"


def write_drawing(folder: Path, chat_id: str, slug: str, kind: str, text: str) -> Path:
    """Keep an approach's latest drawing (a cleaned fragment or a sketch) next to the chat's page."""
    folder.mkdir(parents=True, exist_ok=True)
    path = drawing_path(folder, chat_id, slug, kind)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
