import json
import logging
import os
import tempfile
from pathlib import Path

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.config import STAGES, PhilConfig
from phil.contracts.skills import SkillLabelInput, SkillLabels, SkillToLabel
from phil.packets import PacketTooLarge, build_packet
from phil.skills.discover import Skill
from phil.store.paths import phil_home

logger = logging.getLogger(__name__)

# Bump when skill_labels.md changes: every cached label is then out of date.
LABEL_PROMPT_VERSION = 1


def labels_path() -> Path:
    return phil_home() / "skills.json"


def _key(content_hash: str) -> str:
    return f"{LABEL_PROMPT_VERSION}:{content_hash}"


class LabelCache:
    """Stage labels by skill content hash, kept in ~/.phil/skills.json. An empty list is a label too:
    a skill that helps no stage of software work."""

    def __init__(self, labels: dict[str, list[str]] | None = None) -> None:
        self._labels = labels or {}

    @classmethod
    def load(cls) -> "LabelCache":
        try:
            data = json.loads(labels_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(
            {
                key: value
                for key, value in data.items()
                if isinstance(key, str) and isinstance(value, list) and all(isinstance(s, str) for s in value)
            }
        )

    def get(self, content_hash: str) -> list[str] | None:
        return self._labels.get(_key(content_hash))

    def put(self, content_hash: str, stages: list[str]) -> None:
        self._labels[_key(content_hash)] = list(stages)

    def save(self) -> None:
        path = labels_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".skills-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._labels, handle, indent=2, sort_keys=True)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


def stages_for(skill: Skill, cache: LabelCache, config: PhilConfig) -> tuple[list[str] | None, str]:
    """The skill's stages and where they came from: "override", "label" or "unlabelled" (stages None)."""
    override = config.skills.stages.get(skill.name)
    if override is not None:
        return list(override), "override"
    cached = cache.get(skill.content_hash)
    if cached is not None:
        return cached, "label"
    return None, "unlabelled"


def _label_batch(ctx: AgentContext, batch: list[Skill], model: str, cache: LabelCache) -> int:
    """Label `batch` in one call, halving it while its packet is too big. Returns how many got labels."""
    contract = SkillLabelInput(
        skills=[SkillToLabel(name=s.name, description=s.description) for s in batch], stages=list(STAGES)
    )
    try:
        packet = build_packet("classifier", contract, budget_tokens=ctx.config.budget_for("classifier").max_input_tokens)
    except PacketTooLarge:
        if len(batch) == 1:
            logger.info("skill %s is too large to label; skipping it", batch[0].name)
            return 0
        middle = len(batch) // 2
        return _label_batch(ctx, batch[:middle], model, cache) + _label_batch(ctx, batch[middle:], model, cache)
    out = invoke_agent(get_spec("skill_labels"), packet, ctx, node="skill_labels", model=model)
    assert isinstance(out, SkillLabels)
    by_name = {label.name: list(dict.fromkeys(label.stages)) for label in out.labels}
    labelled = 0
    for skill in batch:
        if skill.name in by_name:
            cache.put(skill.content_hash, by_name[skill.name])
            labelled += 1
        else:
            logger.info("the model returned no label for skill %s", skill.name)
    cache.save()
    return labelled


def label_skills(ctx: AgentContext, skills: list[Skill], cache: LabelCache, batch: int = 25) -> int:
    """Label every skill the cache has no label for, `batch` at a time, saving after each batch.
    Returns how many it labelled. A failure is logged and stops labelling; saved labels stay."""
    pending = [skill for skill in skills if cache.get(skill.content_hash) is None]
    if not pending:
        return 0
    config = ctx.config
    try:
        model = config.tier_model("low") if config.is_systemone("classifier") else config.model_for("classifier")
    except Exception:
        logger.warning("no model to label skills with", exc_info=True)
        return 0
    if model is None:
        logger.info("no low model to label skills with")
        return 0
    labelled = 0
    for start in range(0, len(pending), batch):
        try:
            labelled += _label_batch(ctx, pending[start : start + batch], model, cache)
        except Exception:
            logger.warning("skill labelling failed", exc_info=True)
            break
    return labelled
