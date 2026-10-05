from phil.agents.fake import FakeAgentFactory, ScriptedAgentFactory, Turn
from phil.agents.invoke import AgentContext
from phil.agents.registry import DESIGN_MAX_MODEL_CALLS, get_spec
from phil.agents.spec import load_prompt
from phil.agents.tools import CommandLog
from phil.chat.design import propose_approaches
from phil.config import CHAT_ROLES, DEFAULT_TIERS, ROLES, PhilConfig
from phil.contracts import Approach, Approaches, DesignInput, Goal
from phil.store.db import connect

GOAL = Goal(objective="Add a CTA section to the home page", approach_open=True)
APPROACHES = Approaches(
    options=[Approach(name="Footer band", summary="A band above the footer."), Approach(name="Inline card", summary="A card after the hero.")],
    recommended=1,
)
LEGACY_SIX = ("orchestrator", "architect", "critic", "implementer", "tester", "reviewer")


def ctx_for(tmp_path, config, factory):
    return AgentContext(config=config, conn=connect(tmp_path / "t.db"), layer="chat", factory=factory)


def test_the_designer_reads_the_tree_and_returns_approaches(tmp_path):
    seen: list[Turn] = []

    def design(turn: Turn) -> Approaches:
        seen.append(turn)
        return APPROACHES

    config = PhilConfig(models={"low": "openrouter:l", "high": "openrouter:h"})
    out = propose_approaches(ctx_for(tmp_path, config, ScriptedAgentFactory({"design": [design]})), GOAL, tree=tmp_path, overview="Tracked files:\nindex.html")
    assert out == APPROACHES
    assert seen[0].workdir == tmp_path and set(seen[0].tools) == {"run_shell"}
    packet = seen[0].payload["messages"][0]["content"]
    assert "Add a CTA section to the home page" in packet and "Tracked files:" in packet


def test_the_design_spec_is_light_read_only_and_capped():
    spec = get_spec("design")
    assert (spec.role, spec.harness, spec.read_only_shell, spec.writes_files) == ("designer", "light", True, False)
    assert (spec.in_contract, spec.out_contract) == (DesignInput, Approaches)
    assert spec.max_model_calls == DESIGN_MAX_MODEL_CALLS == 8
    assert load_prompt(spec).startswith("# Role: Designer")


def test_the_designer_shell_cannot_write(tmp_path):
    from phil.agents.invoke import _shell_for

    run = _shell_for(get_spec("design"), PhilConfig(models={"high": "openrouter:h"}), tmp_path, CommandLog(), approved=("touch y",))
    assert run("ls").startswith("exit_code: 0")
    assert run("touch y").startswith("DENIED")


def test_the_designer_role_resolves_through_the_high_tier():
    assert "designer" in ROLES and DEFAULT_TIERS["designer"] == "high"
    assert "designer" not in CHAT_ROLES
    assert PhilConfig(models={"high": "openrouter:h", "low": "openrouter:l"}).model_for("designer") == "openrouter:h"


def test_without_a_designer_model_the_designer_uses_the_architects(tmp_path):
    factory = FakeAgentFactory([APPROACHES])
    config = PhilConfig(models={role: f"openrouter:{role}/model" for role in LEGACY_SIX})
    propose_approaches(ctx_for(tmp_path, config, factory), GOAL, tree=tmp_path, overview="")
    assert factory.built == [("design", "openrouter:architect/model")]
