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


def test_a_writing_spec_keeps_the_project_allowlist_and_approvals(tmp_path):
    from phil.agents.invoke import _shell_for

    config = PhilConfig(models={"low": "openrouter:l"})
    run = _shell_for(get_spec("implementer"), config, tmp_path, CommandLog(), approved=("touch y",))
    assert not run("touch y").startswith("DENIED")
    assert (tmp_path / "y").exists()
