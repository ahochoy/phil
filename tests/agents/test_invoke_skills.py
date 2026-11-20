"""invoke_agent and skills: a tool agent gets a list and `load_skill`; a lean agent gets up to two
skills' SKILL.md text in its packet, never past its budget."""

import dataclasses
import hashlib
from pathlib import Path

import pytest

from phil.agents.factory import BUDGET_GRACE_CALLS, build_agent
from phil.agents.fake import FakeAgent
from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.agents.spec import load_prompt
from phil.config import PhilConfig, RoleBudget
from phil.contracts import ArchitectInput, Goal
from phil.contracts.routing import Answer, AnswerInput
from phil.packets import build_packet
from phil.packets.packet import estimate_tokens
from phil.skills.discover import Catalog, Skill
from phil.skills.labels import LabelCache
from phil.skills.runtime import MIN_CAP_FOR_SKILLS, SkillRuntime, load_runtime
from phil.skills.select import INLINE_TOKEN_CAP, INLINED_TRUST_SENTENCE, TRUST_SENTENCE
from phil.skills.sources import repo_digest, set_repo_approved
from phil.store.paths import ProjectPaths
from tests.agents.conftest import critique
from tests.agents.test_light_harness import ToolRecordingModel
from tests.agents.test_model_retry import tool_call
from tests.helpers import TEST_MODELS


def make_skill(root: Path, name: str, description: str, body: str = "Do the thing well.") -> Skill:
    folder = root / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8")
    digest = hashlib.sha256((folder / "SKILL.md").read_bytes()).hexdigest()
    return Skill(name, description, folder.resolve(), "agents", digest)


def runtime(*labelled: tuple[Skill, list[str]]) -> SkillRuntime:
    cache = LabelCache()
    for skill, stages in labelled:
        cache.put(skill.content_hash, stages)
    return SkillRuntime(catalog=Catalog(skills=[skill for skill, _ in labelled]), cache=cache)


class Stop(Exception):
    pass


class RecordingFactory:
    """Records the context, the prompt and the tool names each agent was built with."""

    def __init__(self, outputs: list[object]):
        self.outputs = outputs
        self.contexts: list[str] = []
        self.prompts: list[str] = []
        self.tools: list[list[str]] = []
        self.agent: FakeAgent | None = None

    def __call__(self, spec, model, workdir, tools, *, timeout_s=180, provider=None, **_):
        self.contexts.append(spec.context)
        self.prompts.append(load_prompt(spec))
        self.tools.append([tool.__name__ for tool in tools])
        self.agent = FakeAgent(list(self.outputs))
        return self.agent


def answer_packet():
    return build_packet("answerer", AnswerInput(question="how should the subtract layout look?"), budget_tokens=4000)


def ctx_for(config, conn, tmp_path, factory, **overrides):
    return AgentContext(config=config, conn=conn, layer="chat", workdir=tmp_path, factory=factory, **overrides)


def ask(config, conn, tmp_path, **overrides) -> RecordingFactory:
    factory = RecordingFactory([Answer(text="Like this.", files=[])])
    ctx = ctx_for(config, conn, tmp_path, factory, **overrides)
    invoke_agent(get_spec("answer"), answer_packet(), ctx, node="answer")
    return factory


def test_tool_agent_gets_list_and_tool(config, conn, tmp_path):
    layout = make_skill(tmp_path, "subtract-layout", "Lay out subtract pages")
    factory = ask(config, conn, tmp_path, skills=runtime((layout, ["implementing"])), repo_context="CTX")
    [context] = factory.contexts
    assert context == f"CTX\n\n## Skills\n\n{TRUST_SENTENCE}\n\n- subtract-layout: Lay out subtract pages"
    assert factory.tools == [["run_shell", "load_skill"]]
    assert factory.prompts[0].rstrip().endswith("- subtract-layout: Lay out subtract pages")


def test_tool_agent_list_is_the_whole_context_without_a_repo_context(config, conn, tmp_path):
    layout = make_skill(tmp_path, "subtract-layout", "Lay out subtract pages")
    factory = ask(config, conn, tmp_path, skills=runtime((layout, ["implementing"])), repo_context="")
    assert factory.contexts == [f"## Skills\n\n{TRUST_SENTENCE}\n\n- subtract-layout: Lay out subtract pages"]


def test_no_skills_no_change(config, conn, tmp_path):
    today = ask(config, conn, tmp_path, repo_context="CTX")
    design_only = make_skill(tmp_path, "subtract-layout", "Lay out subtract pages")
    unmatched = ask(config, conn, tmp_path, repo_context="CTX", skills=runtime((design_only, ["testing"])))
    assert today.contexts == unmatched.contexts == ["CTX"]
    assert today.tools == unmatched.tools == [["run_shell"]]


def test_revise_cap_gets_no_load_skill(config, conn, tmp_path):
    assert MIN_CAP_FOR_SKILLS == 5
    plan_skill = make_skill(tmp_path, "planning-help", "Plan subtract work")
    spec = dataclasses.replace(get_spec("architect"), max_model_calls=4)
    factory = RecordingFactory([Stop()])
    packet = build_packet("architect", ArchitectInput(goal=Goal(objective="Plan subtract")), budget_tokens=8000)
    ctx = ctx_for(config, conn, tmp_path, factory, skills=runtime((plan_skill, ["planning"])), repo_context="")
    with pytest.raises(Stop):
        invoke_agent(spec, packet, ctx, node="architect")
    assert factory.tools == [[]] and factory.contexts == [""]  # the architect has no shell tool


def test_capped_last_call_has_no_load_skill(config, conn, tmp_path, monkeypatch):
    # Through the real light harness: load_skill is offered until the last allowed call, which has
    # only the answer tool.
    layout = make_skill(tmp_path, "subtract-layout", "Lay out subtract pages")
    spec = dataclasses.replace(get_spec("answer"), max_model_calls=MIN_CAP_FOR_SKILLS)
    reads = [tool_call("ls", {"path": "/"}, f"c{i}") for i in range(MIN_CAP_FOR_SKILLS - 1)]
    model = ToolRecordingModel(script=[*reads, tool_call("Answer", {"text": "Like this.", "files": []}, "a")])
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    ctx = ctx_for(config, conn, tmp_path, build_agent, skills=runtime((layout, ["implementing"])), repo_context="")
    assert invoke_agent(spec, answer_packet(), ctx, node="answer").text == "Like this."
    assert len(model.bound) == MIN_CAP_FOR_SKILLS < MIN_CAP_FOR_SKILLS + BUDGET_GRACE_CALLS
    assert "load_skill" in model.bound[0] and "load_skill" in model.bound[-2]
    assert model.bound[-1] == ["Answer"]


# --- lean agents -------------------------------------------------------------


def critic_packet():
    from phil.contracts import CriticInput, Plan, Task

    task = Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"])
    plan = Plan(keyword="CALC", description="Add subtract", tasks=[task])
    return build_packet("critic", CriticInput(goal=Goal(objective="Add subtract"), plan=plan), budget_tokens=4000)


def criticise(conn, tmp_path, skills, *, budget=12_000, notice=None, packet=None, artifacts=None):
    config = PhilConfig(models=TEST_MODELS, budget={"critic": RoleBudget(max_input_tokens=budget)})
    factory = RecordingFactory([critique()])
    ctx = AgentContext(
        config=config, conn=conn, layer="run", factory=factory, skills=skills, notice=notice, artifacts=artifacts
    )
    invoke_agent(get_spec("critic"), packet or critic_packet(), ctx, node="critic")
    assert factory.tools == [[]]
    assert factory.agent is not None
    return factory.agent.calls[0]["messages"][0]["content"]


def test_lean_agent_gets_inlined_skill(conn, tmp_path, artifacts):
    helper = make_skill(tmp_path, "subtract-helper", "Add subtract functions", "Check both signs.")
    content = criticise(conn, tmp_path, runtime((helper, ["reviewing"])), artifacts=artifacts)
    assert content.endswith(
        f"## Skills\n\n{INLINED_TRUST_SENTENCE}\n\n## Skill: subtract-helper\n---\nname: subtract-helper\n"
        "description: Add subtract functions\n---\nCheck both signs.\n"
    )
    assert content.count("## Skills") == 1
    assert "load_skill" not in content  # a lean agent has no tools
    saved = (artifacts.run_dir / "packets").glob("*.json")
    assert any("## Skill: subtract-helper" in path.read_text() for path in saved)


def test_lean_agent_trust_sentence_once_for_two_skills(conn, tmp_path):
    first = make_skill(tmp_path, "subtract-helper", "Add subtract functions")
    second = make_skill(tmp_path, "subtract-checks", "Check subtract plans")
    content = criticise(conn, tmp_path, runtime((first, ["reviewing"]), (second, ["planning"])))
    assert content.count(INLINED_TRUST_SENTENCE) == 1 and content.count("## Skills") == 1
    assert "## Skill: subtract-helper" in content and "## Skill: subtract-checks" in content
    assert "load_skill" not in content


def approved_repo(tmp_path):
    """A repo with one approved skill, its runtime (labelled for reviewing), and the skill's folder."""
    root = tmp_path / "repo"
    folder = root / ".agents" / "skills" / "subtract-helper"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: subtract-helper\ndescription: Add subtract functions\n---\nCheck both signs.\n", encoding="utf-8"
    )
    paths = ProjectPaths("repo", home=tmp_path / "home")
    set_repo_approved(paths, True, repo_digest(root))
    loaded = load_runtime(PhilConfig(), root, paths)
    assert loaded is not None
    [skill] = loaded.catalog.skills
    loaded.cache.put(skill.content_hash, ["reviewing"])
    return loaded, folder


def test_a_repo_skill_edited_mid_run_is_not_inlined(conn, tmp_path):
    loaded, folder = approved_repo(tmp_path)
    assert "## Skill: subtract-helper" in criticise(conn, tmp_path, loaded)
    (folder / "notes.md").write_text("added by an agent", encoding="utf-8")  # SKILL.md itself is unchanged
    assert "## Skills" not in criticise(conn, tmp_path, loaded)


def test_a_change_during_the_read_is_discarded(conn, tmp_path):
    helper = make_skill(tmp_path, "subtract-helper", "Add subtract functions", "Check both signs.")
    skills = runtime((helper, ["reviewing"]))
    answers = iter([True, False])  # approved before the read, not after it
    skills.still_approved = lambda _: next(answers)  # type: ignore[method-assign]
    notices: list[str] = []
    content = criticise(conn, tmp_path, skills, notice=notices.append)
    assert "Check both signs." not in content and "## Skills" not in content
    assert notices == []


def test_a_changed_skill_md_is_not_inlined(conn, tmp_path):
    helper = make_skill(tmp_path, "subtract-helper", "Add subtract functions", "Check both signs.")
    (helper.path / "SKILL.md").write_text("---\nname: subtract-helper\ndescription: x\n---\nEdited.\n")
    content = criticise(conn, tmp_path, runtime((helper, ["reviewing"])))
    assert "Edited." not in content and "## Skills" not in content


def test_an_unmatched_skill_is_not_inlined(conn, tmp_path):
    other = make_skill(tmp_path, "kitchen-recipes", "Bake bread loaves")
    content = criticise(conn, tmp_path, runtime((other, ["reviewing"])))
    assert "## Skills" not in content


def test_a_long_skill_is_cut_to_the_cap(conn, tmp_path):
    long = make_skill(tmp_path, "subtract-helper", "Add subtract functions", "x" * (INLINE_TOKEN_CAP * 4 + 500))
    content = criticise(conn, tmp_path, runtime((long, ["reviewing"])), budget=48_000)
    section = content.split("## Skill: subtract-helper\n", 1)[1]
    text, marker = section.rstrip("\n").rsplit("\n", 1)
    assert marker == "[skill text cut to fit]"
    assert estimate_tokens(text) <= INLINE_TOKEN_CAP


def test_a_refused_skill_read_is_skipped(conn, tmp_path, caplog):
    helper = make_skill(tmp_path, "subtract-helper", "Add subtract functions")
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    (helper.path / "SKILL.md").unlink()
    (helper.path / "SKILL.md").symlink_to(outside)
    with caplog.at_level("INFO"):
        content = criticise(conn, tmp_path, runtime((helper, ["reviewing"])))
    assert "secret" not in content and "## Skills" not in content
    assert "subtract-helper" in caplog.text


def test_inlined_skills_never_overflow_the_budget(conn, tmp_path):
    first = make_skill(tmp_path, "subtract-helper", "Add subtract functions", "a" * 800)
    second = make_skill(tmp_path, "subtract-checks", "Check subtract plans", "b" * 800)
    skills = runtime((first, ["reviewing"]), (second, ["planning"]))
    packet = critic_packet()
    # Each skill costs over 200 tokens: room for one, then for none.
    both = criticise(conn, tmp_path, skills, budget=packet.tokens + 1000)
    assert both.count("## Skill: ") == 2
    first_listed = both.split("## Skill: ", 2)[1].split("\n", 1)[0]
    one = criticise(conn, tmp_path, skills, budget=packet.tokens + 350)
    assert one.count("## Skill: ") == 1 and f"## Skill: {first_listed}\n" in one  # the second went first
    none = criticise(conn, tmp_path, skills, budget=packet.tokens + 5)
    assert "## Skills" not in none


def test_feed_notice_for_inlined_skill(conn, tmp_path):
    helper = make_skill(tmp_path, "subtract-helper", "Add subtract functions")
    notices: list[str] = []
    criticise(conn, tmp_path, runtime((helper, ["reviewing"])), notice=notices.append)
    assert notices == ["Using skill subtract-helper (critic)"]
