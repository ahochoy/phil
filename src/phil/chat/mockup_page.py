"""The mockup page: a plain frame around the model's HTML fragments.

Pure helpers, no controller wiring. The CSP is the real guard against script and
network access; `clean_fragment` only removes the obvious cases.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from pathlib import Path

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "form-action 'none'; base-uri 'none'"
)

CLOSING_LINE = "Pick in Phil: Enter or a number to choose, `show N <note>` to redraw."

_FLAGS = re.IGNORECASE | re.DOTALL
_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script\s*>", _FLAGS)
_SCRIPT_TAG = re.compile(r"</?script\b[^>]*>?", _FLAGS)
_PAIRED_EMBED = re.compile(r"<(iframe|object)\b[^>]*>.*?</\1\s*>", _FLAGS)
_STRAY_TAG = re.compile(r"</?(?:iframe|object|embed|form|meta|base|link)\b[^>]*>?", _FLAGS)
_HANDLER = re.compile(r"""[\s/]+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]*)""", _FLAGS)
_URL_ATTR = re.compile(
    r"""(?<=[\s/])(href|src|action|formaction|xlink:href)\s*=\s*("[^"]*"|'[^']*'|[^\s>]*)""", _FLAGS
)
_JS_URL = re.compile(r"javascript\s*:", _FLAGS)

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


def _safe_url_attr(match: re.Match[str]) -> str:
    value = match.group(2).strip("\"'").strip().lower()
    return match.group(0) if value.startswith(("#", "data:")) else ""


def _clean_once(fragment: str) -> str:
    out = _SCRIPT_BLOCK.sub("", fragment)
    out = _SCRIPT_TAG.sub("", out)
    out = _PAIRED_EMBED.sub("", out)
    out = _STRAY_TAG.sub("", out)
    out = _HANDLER.sub("", out)
    out = _URL_ATTR.sub(_safe_url_attr, out)
    return _JS_URL.sub("", out)


def clean_fragment(fragment: str) -> str:
    """Remove scripts, navigation and embedding tags, handlers and unsafe URLs.

    Handles upper and mixed case, `\\r\\n` line endings and unclosed tags. Repeats
    until the output is stable, so removing one piece can't assemble another.
    """
    out = fragment
    while (cleaned := _clean_once(out)) != out:
        out = cleaned
    return out


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
