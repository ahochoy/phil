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

    (tmp_path / "calc.py").write_text("def add(a, b): ...\n")
    factory = ScriptedAgentFactory({"answer": [answer]})
    ctx = AgentContext(config=PhilConfig(models={"low": "openrouter:l", "high": "openrouter:h"}),
                       conn=connect(tmp_path / "t.db"), layer="chat", factory=factory)
    out = ask_answer(ctx, "what does add do?", root=tmp_path, overview="Tracked files:\ncalc.py")
    assert out.files == ["calc.py"]
    assert seen[0].workdir == tmp_path and set(seen[0].tools) == {"run_shell"}
    packet = seen[0].payload["messages"][0]["content"]
    assert "what does add do?" in packet and "Tracked files:" in packet


def test_answer_files_come_from_what_the_answerer_read(tmp_path):
    # A live answer (bench explain-module) named its file only in the text: `files` came back empty.
    (tmp_path / "calc").mkdir()
    (tmp_path / "calc" / "__init__.py").write_text("def divide(a, b): ...\n")
    (tmp_path / "README.md").write_text("calc\n")
    log = CommandLog()

    def answer(turn: Turn) -> Answer:
        # What the file tools record while the answerer works (virtual paths, rooted at `/`).
        log.tool_paths["read_file"] = ["/calc/__init__.py", "/gone.py"]
        log.tool_paths["ls"] = ["/"]
        return Answer(text="divide raises ZeroDivisionError.", files=["/README.md"])

    factory = ScriptedAgentFactory({"answer": [answer]})
    ctx = AgentContext(config=PhilConfig(models={"low": "openrouter:l", "high": "openrouter:h"}),
                       conn=connect(tmp_path / "t.db"), layer="chat", factory=factory, command_log=log)
    out = ask_answer(ctx, "what does divide do?", root=tmp_path, overview="")
    # The model's own list first (made repo-relative), then files it read; listings and missing files left out.
    assert out.files == ["README.md", "calc/__init__.py"]


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


def test_a_deny_rule_blocks_a_built_in_read_for_the_answerer(tmp_path):
    from datetime import date

    from phil.agents.invoke import _shell_for
    from phil.permissions.store import add_rule, load_policy

    add_rule(tmp_path / "global.toml", "deny", "git status *", "manual", today=date(2026, 10, 13))
    policy = load_policy(repo_name="calc", project_policy=tmp_path / "proj.toml",
                         global_policy=tmp_path / "global.toml", config_allow=["git *"])
    log = CommandLog()
    run = _shell_for(get_spec("answer"), PhilConfig(models={"low": "openrouter:l"}), tmp_path, log, policy=policy)
    assert run("git status").startswith("DENIED: `git status` is blocked by your deny rule")
    assert log.blocked == ["git status"]
    assert run("git fetch").startswith("DENIED: `git fetch` is not read-only")  # no allow rule applies here...
    assert run("ls").startswith("exit_code: 0")  # ...but built-in reads do


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
