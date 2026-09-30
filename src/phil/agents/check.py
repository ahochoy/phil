"""`phil models check`: one tiny real call per model a role uses, through the agent path Phil uses."""

import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from phil.agents.invoke import AgentContext, AgentFactory, ContractViolation, invoke_agent
from phil.agents.providers import missing_key_message, provider_for_model
from phil.agents.spec import AgentSpec
from phil.config import ROLES, TIERS, ConfigError, PhilConfig
from phil.contracts import Contract
from phil.packets import build_packet
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect

CHECK_WORD = "pineapple"
EXCERPT_CHARS = 120
NO_STRUCTURED_OUTPUT = "no structured output was returned"


class ModelCheckInput(Contract):
    word: str = Field(description="The word to echo back.")


class ModelCheck(Contract):
    ok: bool = Field(description="Always true.")
    echo: str = Field(description="The input's word, exactly as given.")


# Lean, and without _shared.md: its self_check rules don't apply to this tiny contract. A text
# answer ends the check at once (one model call), reported with an excerpt of the text.
MODEL_CHECK = AgentSpec(
    "model_check", "orchestrator", ModelCheckInput, ModelCheck,
    harness="lean", shared_prompt=False, end_on_text=True,
)


@dataclass(frozen=True)
class CheckResult:
    label: str  # the tiers and `role:<name>` keys that use `model`, e.g. "high, low"
    model: str
    ok: bool
    seconds: float
    detail: str  # why it failed; empty when ok


def _role_labels(config: PhilConfig) -> dict[str, str]:
    """Label -> model for each model some role resolves to: the tier the role uses, or
    `role:<name>` when the role's own [models] key overrides its tier. Roles with no model are skipped."""
    used: dict[str, str] = {}
    for role in ROLES:
        try:
            model = config.model_for(role)
        except ConfigError:
            continue
        used[f"role:{role}" if role in config.models else config.model_owner(role)] = model
    return used


def check_targets(config: PhilConfig) -> list[tuple[str, list[str]]]:
    """Each distinct model some role resolves to, with the labels that use it: the tiers in tier
    order, then the role keys under [models] (which override their tier), in first-seen order.
    A tier no role uses isn't a target (see `unused_tiers`)."""
    used = _role_labels(config)
    labels: dict[str, list[str]] = {}
    for label in (*TIERS, *(f"role:{role}" for role in ROLES)):
        if label in used:
            labels.setdefault(used[label], []).append(label)
    return list(labels.items())


def unused_tiers(config: PhilConfig) -> list[tuple[str, str]]:
    """(tier, model) for each tier set under [models] that no role maps to; it is not called."""
    used = _role_labels(config)
    return [(tier, config.models[tier]) for tier in TIERS if tier in config.models and tier not in used]


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, ConfigError):  # Phil's own message (unknown provider, missing key) says it all
        return _first_line(str(exc))
    return f"{type(exc).__name__}: {_first_line(str(exc))}"


def _violation_detail(exc: ContractViolation) -> str:
    raw = None
    if exc.rejected_path is not None:
        raw = json.loads(Path(exc.rejected_path).read_text()).get("raw")
    if exc.problems == [NO_STRUCTURED_OUTPUT]:
        if isinstance(raw, str) and raw.strip():
            excerpt = " ".join(raw.split())[:EXCERPT_CHARS]
            return f'returned text instead of the required structured output: "{excerpt}"'
        return "returned no structured output"
    return f"returned invalid structured output: {'; '.join(exc.problems)}"


def _check_one(ctx: AgentContext, model: str, labels: list[str], call: int) -> tuple[bool, str]:
    packet = build_packet("model_check", ModelCheckInput(word=CHECK_WORD), budget_tokens=1_000)
    try:
        # Checked here so the errors name the tiers and roles using the model, not the check's own role.
        provider = provider_for_model(ctx.config, model, labels[0])
        if provider.api_key_env and not os.environ.get(provider.api_key_env):
            return False, missing_key_message(provider.name, provider.api_key_env, labels)
        output = invoke_agent(
            MODEL_CHECK, packet, ctx, node="model_check", call=call,
            model=model, max_attempts=1, transient_retries=False,
        )
    except ContractViolation as exc:
        return False, _violation_detail(exc)
    except Exception as exc:
        return False, _error_detail(exc)
    assert isinstance(output, ModelCheck)
    if not output.ok:
        return False, "returned ok=false"
    if output.echo.strip() != CHECK_WORD:
        return False, f'echoed "{output.echo}" instead of "{CHECK_WORD}"'
    return True, ""


def check_models(
    config: PhilConfig,
    *,
    factory: AgentFactory | None = None,
    repo_root: Path,
    clock: Callable[[], float] = time.monotonic,
) -> list[CheckResult]:
    """One call per distinct model a role resolves to (see `check_targets`): a lean agent asked to return
    `ModelCheck(ok=true, echo=<word>)`, with the configured timeout, one try and no retries. Its
    telemetry and artifacts go to a temporary directory, not the project's."""
    targets = check_targets(config)
    if not targets:
        return []
    results: list[CheckResult] = []
    with tempfile.TemporaryDirectory(prefix="phil-models-check-") as scratch:
        conn = connect(Path(scratch) / "check.db")
        try:
            ctx = AgentContext(
                config=config,
                conn=conn,
                layer="chat",
                artifacts=ArtifactStore(Path(scratch) / "artifacts"),
                workdir=repo_root,
                factory=factory,
            )
            for call, (model, labels) in enumerate(targets, 1):
                started = clock()
                ok, detail = _check_one(ctx, model, labels, call)
                results.append(CheckResult(", ".join(labels), model, ok, clock() - started, detail))
        finally:
            conn.close()
    return results
