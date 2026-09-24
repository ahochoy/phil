from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from phil.agents.spec import AgentSpec


@dataclass
class FakeMessage:
    usage_metadata: dict | None = None
    response_metadata: dict = field(default_factory=dict)
    content: str = ""


class FakeAgent:
    def __init__(self, outputs: list[object], usage: tuple[int, int, float] = (100, 20, 0.0)) -> None:
        self.outputs = list(outputs)
        self.usage = usage
        self.calls: list[dict] = []

    def invoke(self, payload: dict) -> dict:
        self.calls.append(payload)
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        input_tokens, output_tokens, cost = self.usage
        message = FakeMessage(
            usage_metadata={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            response_metadata={"cost": cost},
        )
        return {"messages": [message], "structured_response": output}


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
