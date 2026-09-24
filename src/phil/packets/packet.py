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


class Packet(BaseModel):
    role: str
    contract_type: str
    contract_json: str
    files: dict[str, str]
    ledger: list[str]
    omitted: list[str]
    tokens: int = 0

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
        return "".join(parts)


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
) -> Packet:
    contract_type = type(contract).__name__
    contract_json = contract.model_dump_json(indent=2)
    used = estimate_tokens(_contract_section(contract_type, contract_json))
    if used > budget_tokens:
        raise PacketTooLarge(f"{contract_type} alone needs {used} tokens; budget is {budget_tokens}")
    # Reserve room for the omitted section so the rendered packet stays within budget.
    reserve = estimate_tokens(_OMITTED_HEADER) + sum(estimate_tokens(f"- {path} (not found)\n") for path in files) + 10

    kept_files: dict[str, str] = {}
    omitted: list[str] = []
    header_cost = estimate_tokens(_FILES_HEADER)
    for relative in files:
        if root is None:
            raise ValueError("root is required when files are given")
        path = _resolve_under(root, relative)
        if not path.is_file():
            omitted.append(f"{relative} (not found)")
            continue
        cost = estimate_tokens(_file_section(relative, path.read_text(errors="replace")))
        extra = cost + (header_cost if not kept_files else 0)
        if used + extra + reserve <= budget_tokens:
            kept_files[relative] = path.read_text(errors="replace")
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
        omitted.append(f"{len(ledger) - len(kept_ledger)} ledger entries")

    packet = Packet(
        role=role,
        contract_type=contract_type,
        contract_json=contract_json,
        files=kept_files,
        ledger=kept_ledger,
        omitted=omitted,
    )
    packet.tokens = estimate_tokens(packet.render())
    return packet
