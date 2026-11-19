import base64
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from phil.contracts import Contract


class PacketTooLarge(Exception):
    pass


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _contract_section(contract_type: str, contract_json: str) -> str:
    return f"## Input ({contract_type})\n```json\n{contract_json}\n```\n"


def _file_section(path: str, content: str) -> str:
    return f"### {path}\n```\n{content}\n```\n"


_FILES_HEADER = "\n## Files\n"
_LEDGER_HEADER = "\n## Assumptions so far\n"
_OMITTED_HEADER = "\n## Omitted for budget\n"


IMAGE_TOKENS = 1600  # a flat charge per attached image, toward the budget and the cost estimate


def image_paths_section(paths: Sequence[str | Path]) -> str:
    """The text-only form of the images: their paths, for agents that don't get the pictures."""
    return "Images the user attached:\n" + "".join(f"- {path}\n" for path in paths)


class Packet(BaseModel):
    role: str
    contract_type: str
    contract_json: str
    files: dict[str, str]
    ledger: list[str]
    omitted: list[str]
    tokens: int = 0
    images: list[str] = []
    extra_text: str = ""  # closing text, e.g. the image paths for an agent that gets no pictures

    def render_content(self) -> str | list[dict]:
        """The text, or (with images) the text part followed by one image part per readable image.

        An image that has gone missing is left out and named in the text instead."""
        if not self.images:
            return self.render()
        from phil.chat.attachments import mime_type  # lazy: attachments pulls in the UI helpers

        parts: list[dict] = []
        missing: list[str] = []
        for image in self.images:
            path = Path(image)
            try:
                data = base64.b64encode(path.read_bytes()).decode("ascii")
                mime = mime_type(path)
            except (OSError, KeyError):
                missing.append(f"(image {path.name} is no longer available)\n")
                continue
            parts.append({"type": "image", "base64": data, "mime_type": mime})
        text = self.render() + "".join(missing)
        if not parts:
            return text
        return [{"type": "text", "text": text}, *parts]

    def render(self) -> str:
        parts = [_contract_section(self.contract_type, self.contract_json)]
        if self.files:
            parts.append(_FILES_HEADER)
            parts.extend(_file_section(path, content) for path, content in self.files.items())
        if self.ledger:
            parts.append(_LEDGER_HEADER)
            parts.extend(f"- {entry}\n" for entry in self.ledger)
        if self.omitted:
            parts.append(_OMITTED_HEADER)
            parts.extend(f"- {item}\n" for item in self.omitted)
        if self.extra_text:
            parts.append("\n" + self.extra_text)
        return "".join(parts)


def _with_ledger_summary(omitted: list[str], count: int) -> list[str]:
    kept = [item for item in omitted if not item.endswith(" ledger entries")]
    if count:
        kept.append(f"{count} ledger entries")
    return kept


def _resolve_under(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"file path must be relative and inside the root: {relative!r}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"file path escapes the root: {relative!r}")
    return path


def build_packet(
    role: str,
    contract: Contract,
    *,
    budget_tokens: int,
    root: Path | None = None,
    files: Sequence[str] = (),
    ledger: Sequence[str] = (),
    images: Sequence[Path] = (),
    extra_text: str = "",
) -> Packet:
    contract_type = type(contract).__name__
    contract_json = contract.model_dump_json(indent=2)
    image_tokens = IMAGE_TOKENS * len(images)
    used = estimate_tokens(_contract_section(contract_type, contract_json)) + image_tokens
    used += estimate_tokens(extra_text) if extra_text else 0
    if used > budget_tokens:
        raise PacketTooLarge(f"{contract_type} alone needs {used} tokens; budget is {budget_tokens}")
    if files and root is None:
        raise ValueError("root is required when files are given")
    # Reserve room for the omitted section so the rendered packet stays within budget.
    reserve = estimate_tokens(_OMITTED_HEADER) + sum(estimate_tokens(f"- {path} (not found)\n") for path in files) + 10

    kept_files: dict[str, str] = {}
    omitted: list[str] = []
    header_cost = estimate_tokens(_FILES_HEADER)
    for relative in files:
        assert root is not None  # checked above: files and root is None raises
        try:
            path = _resolve_under(root, relative)
        except ValueError:
            omitted.append(f"{relative} (outside root)")
            continue
        if not path.is_file():
            omitted.append(f"{relative} (not found)")
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        cost = estimate_tokens(_file_section(relative, content))
        extra = cost + (header_cost if not kept_files else 0)
        if used + extra + reserve <= budget_tokens:
            kept_files[relative] = content
            used += extra
        else:
            omitted.append(relative)

    kept_ledger: list[str] = []
    ledger_header_cost = estimate_tokens(_LEDGER_HEADER)
    for entry in ledger:
        extra = estimate_tokens(f"- {entry}\n") + (ledger_header_cost if not kept_ledger else 0)
        if used + extra + reserve > budget_tokens:
            break
        kept_ledger.append(entry)
        used += extra
    if len(kept_ledger) < len(ledger):
        omitted = _with_ledger_summary(omitted, len(ledger) - len(kept_ledger))

    def _build() -> Packet:
        packet = Packet(
            role=role,
            contract_type=contract_type,
            contract_json=contract_json,
            files=kept_files,
            ledger=kept_ledger,
            omitted=omitted,
            images=[str(image) for image in images],
            extra_text=extra_text,
        )
        packet.tokens = estimate_tokens(packet.render()) + image_tokens
        return packet

    packet = _build()
    # The reserve above only accounts for files omitted for "not found"; it does not
    # account for the ledger summary line added after the ledger loop. Enforce the
    # actual budget on the rendered text, trimming ledger entries first (lowest
    # priority), then files, in the same deterministic order they were added.
    while packet.tokens > budget_tokens:
        if kept_ledger:
            kept_ledger.pop()
            omitted = _with_ledger_summary(omitted, len(ledger) - len(kept_ledger))
        elif kept_files:
            last_path = next(reversed(kept_files))
            del kept_files[last_path]
            omitted.append(last_path)
        else:
            raise PacketTooLarge(
                f"packet cannot fit within budget_tokens={budget_tokens} even after "
                "trimming all files and ledger entries"
            )
        packet = _build()
    return packet
