from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cost_usd: float


def extract_usage(messages: Iterable[object]) -> Usage:
    input_tokens = output_tokens = 0
    cost = 0.0
    for message in messages:
        usage = getattr(message, "usage_metadata", None) or {}
        input_tokens += int(usage.get("input_tokens", 0) or 0)
        output_tokens += int(usage.get("output_tokens", 0) or 0)
        metadata = getattr(message, "response_metadata", None) or {}
        cost += float(metadata.get("cost", 0.0) or 0.0)
    return Usage(input_tokens, output_tokens, cost)
