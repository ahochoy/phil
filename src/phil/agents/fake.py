from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from phil.agents.spec import AgentSpec


@dataclass
class FakeMessage:
    usage_metadata: dict | None = None
    response_metadata: dict = field(default_factory=dict)
    content: str = ""


def _usage_message(usage: tuple[int, int, float]) -> FakeMessage:
    input_tokens, output_tokens, cost = usage
    return FakeMessage(
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={"cost": cost},
    )


class FakeAgent:
    def __init__(self, outputs: list[object], usage: tuple[int, int, float] = (100, 20, 0.0)) -> None:
        self.outputs = list(outputs)
        self.usage = usage
        self.calls: list[dict] = []

    def invoke(self, payload: dict) -> dict:
        if not self.outputs:
            raise AssertionError(f"FakeAgent has no scripted output left (call {len(self.calls)})")
        self.calls.append(payload)
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return {"messages": [_usage_message(self.usage)], "structured_response": output}


class FakeAgentFactory:
    def __init__(self, outputs: list[object], usage: tuple[int, int, float] = (100, 20, 0.0)) -> None:
        self.agent = FakeAgent(outputs, usage)
        self.built: list[tuple[str, str]] = []
        self.tools_seen: list[list[str]] = []

    def __call__(
        self, spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]]
    ) -> FakeAgent:
        self.built.append((spec.name, model))
        self.tools_seen.append([tool.__name__ for tool in tools])
        return self.agent


@dataclass
class Turn:
    payload: dict
    workdir: Path | None
    tools: dict[str, Callable[..., str]]


class _ScriptedAgent:
    def __init__(
        self, factory: "ScriptedAgentFactory", role: str, workdir: Path | None, tools: dict[str, Callable[..., str]]
    ) -> None:
        self.factory = factory
        self.role = role
        self.workdir = workdir
        self.tools = tools

    def invoke(self, payload: dict) -> dict:
        script = self.factory.scripts.setdefault(self.role, [])
        if not script:
            raise AssertionError(f"no scripted output left for {self.role}")
        item = script.pop(0)
        self.factory.calls.append((self.role, payload))
        if isinstance(item, BaseException):
            raise item
        output = item(Turn(payload, self.workdir, self.tools)) if callable(item) else item
        return {"messages": [_usage_message(self.factory.usage)], "structured_response": output}


class ScriptedAgentFactory:
    def __init__(self, scripts: dict[str, list[object]], usage: tuple[int, int, float] = (100, 20, 0.0)) -> None:
        self.scripts = {role: list(items) for role, items in scripts.items()}
        self.usage = usage
        self.calls: list[tuple[str, dict]] = []

    def remaining(self) -> dict[str, int]:
        return {role: len(items) for role, items in self.scripts.items()}

    def __call__(
        self, spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]]
    ) -> _ScriptedAgent:
        return _ScriptedAgent(self, spec.name, workdir, {tool.__name__: tool for tool in tools})
