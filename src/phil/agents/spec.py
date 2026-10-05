from dataclasses import dataclass
from importlib.resources import files
from typing import Literal

from phil.contracts import Contract


# What a capped light agent is told once its model-call budget is spent (see phil.agents.factory).
ANSWER_NOW = "Your tool budget is used up. Answer now with what you have found, and say what you didn't check."
FINISH_NOW = (
    "Your tool budget is nearly used up. Finish the change now and return your result; say what you didn't get to."
)
PLAN_NOW = "Your tool budget is used up. Return your plan now from what you have read; note anything you didn't check."
PROPOSE_NOW ="Your tool budget is used up. Propose your approaches now from what you have read."


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str
    in_contract: type[Contract]
    out_contract: type[Contract]
    tools: tuple[str, ...] = ()
    writes_files: bool = False
    # "deep": deepagents with file tools, planning, and subagents, for roles that explore a repo.
    # "lean": a plain LangChain agent for roles that only judge their input; no unused tools or prompts.
    # "light": a plain LangChain agent with deepagents' file tools (read-only for writes_files=False)
    # and Phil's shell tool, but no sub-agent and no summarization: for short, bounded jobs.
    harness: Literal["deep", "lean", "light"] = "deep"
    # Append _shared.md (output discipline, the required self_check) to the role's prompt.
    shared_prompt: bool = True
    # Lean only: end when the model answers in text instead of returning the structured output, so
    # invoke_agent records the text as `raw` and retries with feedback. Without it, LangChain calls a
    # tool-less agent's model again, unchanged, until its recursion limit. (Deep agents have tools,
    # and LangChain already ends their loop on an answer with no tool call.)
    end_on_text: bool = False
    read_only_shell: bool = False  # shell runs only M1's read-only commands (no project allowlist)
    max_model_calls: int | None = None  # light or deep: the last allowed call must answer
    cap_message: str = ANSWER_NOW  # with max_model_calls: the instruction added to the last allowed call
    # The prompt file's stem when it differs from `name`, for a spec that reuses another's prompt
    # (the quick implementer reads implementer.md).
    prompt_name: str | None = None


def _read_prompt(filename: str) -> str:
    return files("phil.prompts").joinpath(filename).read_text()


def load_prompt(spec: AgentSpec) -> str:
    own = _read_prompt(f"{spec.prompt_name or spec.name}.md").rstrip()
    if not spec.shared_prompt:
        return f"{own}\n"
    return f"{own}\n\n{_read_prompt('_shared.md').rstrip()}\n"
