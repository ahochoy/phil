from phil.agents.fake import ScriptedAgentFactory, Turn
from phil.agents.invoke import AgentContext
from phil.agents.registry import get_spec
from phil.agents.tools import CommandLog
from phil.chat.answer import ANSWER_MAX_MODEL_CALLS, ask_answer
from phil.config import PhilConfig
from phil.contracts.routing import Answer
from phil.store.db import connect


def test_ask_answer_returns_the_answer(tmp_path):
    seen: list[Turn] = []

    def answer(turn: Turn) -> Answer:
        seen.append(turn)
        return Answer(text="calc.add sums two ints.", files=["calc.py"])

    factory = ScriptedAgentFactory({"answer": [answer]})
    ctx = AgentContext(config=PhilConfig(models={"low": "openrouter:l", "high": "openrouter:h"}),
                       conn=connect(tmp_path / "t.db"), layer="chat", factory=factory)
    out = ask_answer(ctx, "what does add do?", root=tmp_path, overview="Tracked files:\ncalc.py")
    assert out.files == ["calc.py"]
    assert seen[0].workdir == tmp_path and set(seen[0].tools) == {"run_shell"}
    packet = seen[0].payload["messages"][0]["content"]
    assert "what does add do?" in packet and "Tracked files:" in packet


def test_the_answer_cap_has_one_source():
    assert get_spec("answer").max_model_calls == ANSWER_MAX_MODEL_CALLS == 12


def test_read_only_shell_denies_project_commands_and_writes(tmp_path):
    from phil.agents.invoke import _shell_for

    config = PhilConfig(models={"low": "openrouter:l"})
    run = _shell_for(get_spec("answer"), config, tmp_path, CommandLog(), extra_allow=("npm test",), approved=("rm x",))
    assert run("ls").startswith("exit_code: 0")
    assert run("pytest").startswith("DENIED")  # on the default project allowlist, but not for the answerer
    assert run("npm test").startswith("DENIED")  # extra_allow ignored
    assert run("rm x").startswith("DENIED")  # approvals ignored
    assert run("touch y").startswith("DENIED")


def test_read_only_denial_names_the_read_only_commands(tmp_path):
    from phil.agents.invoke import _shell_for
    from phil.workspace.shell import READ_ONLY

    config = PhilConfig(models={"low": "openrouter:l"})
    denied = _shell_for(get_spec("answer"), config, tmp_path, CommandLog())("pytest")
    assert "Only read-only commands run here: " + ", ".join(READ_ONLY) in denied
    assert "Allowed patterns" not in denied


def test_a_writing_spec_keeps_the_project_allowlist_and_approvals(tmp_path):
    from phil.agents.invoke import _shell_for

    config = PhilConfig(models={"low": "openrouter:l"})
    run = _shell_for(get_spec("implementer"), config, tmp_path, CommandLog(), approved=("touch y",))
    assert not run("touch y").startswith("DENIED")
    assert (tmp_path / "y").exists()


LEGACY_SIX = ("orchestrator", "architect", "critic", "implementer", "tester", "reviewer")


def test_a_legacy_six_role_config_passes_the_chat_check():
    from phil.config import CHAT_ROLES

    config = PhilConfig(models={role: f"openrouter:{role}/model" for role in LEGACY_SIX})
    assert config.missing_model_messages(CHAT_ROLES) == []


def test_without_an_answerer_model_the_answer_uses_the_orchestrators(tmp_path):
    from phil.agents.fake import FakeAgentFactory

    factory = FakeAgentFactory([Answer(text="ok", files=[])])
    config = PhilConfig(models={role: f"openrouter:{role}/model" for role in LEGACY_SIX})
    ctx = AgentContext(config=config, conn=connect(tmp_path / "t.db"), layer="chat", factory=factory)
    ask_answer(ctx, "what?", root=tmp_path, overview="")
    assert factory.built == [("answer", "openrouter:orchestrator/model")]


def test_an_answerer_model_is_used_when_set(tmp_path):
    from phil.agents.fake import FakeAgentFactory

    factory = FakeAgentFactory([Answer(text="ok", files=[])])
    config = PhilConfig(models={"low": "openrouter:l", "orchestrator": "openrouter:o"})
    ctx = AgentContext(config=config, conn=connect(tmp_path / "t.db"), layer="chat", factory=factory)
    ask_answer(ctx, "what?", root=tmp_path, overview="")
    assert factory.built == [("answer", "openrouter:l")]
