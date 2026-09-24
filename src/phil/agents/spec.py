from dataclasses import dataclass
from importlib.resources import files

from phil.contracts import Contract


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str
    in_contract: type[Contract]
    out_contract: type[Contract]
    tools: tuple[str, ...] = ()
    writes_files: bool = False


def _read_prompt(filename: str) -> str:
    return files("phil.prompts").joinpath(filename).read_text()


def load_prompt(spec: AgentSpec) -> str:
    return f"{_read_prompt(f'{spec.name}.md').rstrip()}\n\n{_read_prompt('_shared.md').rstrip()}\n"
