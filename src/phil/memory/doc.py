"""Phil's private per-repo memory file (spec 2026-10-15 §2.2): four sections of one-line bullets.
Everything the parser doesn't recognise is kept byte for byte."""

from dataclasses import dataclass

SECTIONS = ("Commands", "Layout", "Conventions", "Gotchas")
PHIL_MARK = "<!-- phil -->"
MAX_BULLET_CHARS = 200
MAX_FILE_BYTES = 8 * 1024
_MARK_SUFFIX = f" {PHIL_MARK}"
_BY_KEY = {name.casefold(): name for name in SECTIONS}


@dataclass
class Bullet:
    section: str
    text: str
    by_phil: bool


def _section_of(line: str) -> str | None:
    """The known section a `## ` heading names, or None for any other line or heading."""
    if not line.startswith("## "):
        return None
    return _BY_KEY.get(line[3:].strip().casefold())


class MemoryDoc:
    def __init__(self, entries: list[str | Bullet]) -> None:
        self.entries = entries

    @classmethod
    def parse(cls, text: str) -> "MemoryDoc":
        entries: list[str | Bullet] = []
        section: str | None = None
        for line in text.splitlines():
            if line.startswith("#"):
                section = _section_of(line)
                entries.append(line)
            elif section is not None and line.startswith("- "):
                body = line[2:]
                by_phil = body.endswith(_MARK_SUFFIX)
                entries.append(Bullet(section, body.removesuffix(_MARK_SUFFIX).rstrip() if by_phil else body, by_phil))
            else:
                entries.append(line)
        return cls(entries)

    @classmethod
    def new(cls, slug: str) -> "MemoryDoc":
        lines = [f"# Phil memory — {slug}",
                 "<!-- Phil reads this before every agent call. Edit freely; keep it short. -->"]
        for name in SECTIONS:
            lines += ["", f"## {name}"]
        return cls.parse("\n".join(lines) + "\n")

    def bullets(self) -> list[Bullet]:
        return [entry for entry in self.entries if isinstance(entry, Bullet)]

    @property
    def recognised(self) -> bool:
        lines = [entry for entry in self.entries if isinstance(entry, str)]
        has_content = any(line.strip() for line in lines) or bool(self.bullets())
        return not has_content or any(_section_of(line) for line in lines)

    def _heading_index(self, section: str) -> int | None:
        for i, entry in enumerate(self.entries):
            if isinstance(entry, str) and _section_of(entry) == section:
                return i
        return None

    def add(self, bullet: Bullet) -> None:
        heading = self._heading_index(bullet.section)
        if heading is None:
            if self.entries and self.entries[-1] != "":
                self.entries.append("")
            self.entries += [f"## {bullet.section}", bullet]
            return
        insert = heading + 1
        for i in range(heading + 1, len(self.entries)):
            entry = self.entries[i]
            if isinstance(entry, Bullet):
                insert = i + 1
            elif entry.startswith("#"):
                break
        self.entries.insert(insert, bullet)

    def remove(self, text: str, *, phil_only: bool) -> bool:
        for i, entry in enumerate(self.entries):
            if isinstance(entry, Bullet) and entry.text == text and (entry.by_phil or not phil_only):
                del self.entries[i]
                return True
        return False

    def remove_index(self, n: int) -> Bullet:
        """Remove bullet `n` (1-based, in `bullets()` order); IndexError when there's no such bullet."""
        bullets = self.bullets()
        if not 1 <= n <= len(bullets):
            raise IndexError(n)
        target = bullets[n - 1]
        # By identity: two bullets can be equal, and list.remove would take the first.
        del self.entries[next(i for i, entry in enumerate(self.entries) if entry is target)]
        return target

    def fit(self, max_bytes: int = MAX_FILE_BYTES) -> list[Bullet]:
        """Drop Phil's bullets (section order, then file order) until the file fits; never the user's."""
        dropped: list[Bullet] = []
        order = sorted(
            (b for b in self.bullets() if b.by_phil), key=lambda b: SECTIONS.index(b.section)
        )
        for bullet in order:
            if len(self.render().encode("utf-8")) <= max_bytes:
                break
            del self.entries[next(i for i, entry in enumerate(self.entries) if entry is bullet)]
            dropped.append(bullet)
        return dropped

    def render(self) -> str:
        lines = [
            (f"- {e.text}{_MARK_SUFFIX}" if e.by_phil else f"- {e.text}") if isinstance(e, Bullet) else e
            for e in self.entries
        ]
        return "\n".join(lines) + "\n" if lines else ""

    def prompt_text(self) -> str:
        """What agents see: no title, comments or marks, and no known section left empty."""
        out: list[str] = []
        pending_heading: str | None = None
        for entry in self.entries:
            if isinstance(entry, Bullet):
                if pending_heading is not None:
                    out.append(pending_heading)
                    pending_heading = None
                out.append(f"- {entry.text}")
                continue
            stripped = entry.strip()
            if entry.startswith("# ") or (stripped.startswith("<!--") and stripped.endswith("-->")):
                continue
            if _section_of(entry):
                pending_heading = entry
                continue
            if entry.startswith("#"):
                pending_heading = None
            out.append(entry)
        text = "\n".join(out)
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        return text.strip()

    def annotated(self) -> str:
        """The memory for the memory agent: each bullet tagged `[phil]` or `[you]`, by section."""
        return "\n".join(
            f"{b.section}: [{'phil' if b.by_phil else 'you'}] {b.text}" for b in self.bullets()
        )
