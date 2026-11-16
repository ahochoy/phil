from phil.agents.fake import ScriptedAgentFactory
from phil.agents.registry import CONVERSE_MAX_MODEL_CALLS, get_spec
from phil.agents.spec import REPLY_NOW, load_prompt
from phil.chat.planning import converse
from phil.contracts import ConverseInput, ConverseTurn, IntakeTurn


def test_converse_spec():
    spec = get_spec("converse")
    assert (spec.role, spec.in_contract, spec.out_contract, spec.harness, spec.tools) == (
        "orchestrator", ConverseInput, IntakeTurn, "light", ("shell",)
    )
    assert spec.read_only_shell and spec.repo_context and not spec.writes_files and not spec.shared_prompt
    assert (spec.max_model_calls, spec.cap_message) == (CONVERSE_MAX_MODEL_CALLS, REPLY_NOW) == (6, REPLY_NOW)
    assert "# Role: Intake conversation" in load_prompt(spec)


def test_converse_sends_the_conversation_and_reads_the_snapshot(chat_ctx, tmp_path):
    out = IntakeTurn(reply="Which page?", choices=["Home", "Pricing"], ready=False)
    factory = ScriptedAgentFactory({"converse": [out]})
    turns = [ConverseTurn(speaker="user", text="add a banner")]
    got = converse(
        chat_ctx(factory), turns, tree=tmp_path, overview="calc.py", route_depth="quick",
        route_class="simple_change", detected_test_cmd="pytest", must_decide=True, call=3,
    )
    assert got == out
    role, payload = factory.calls[0]
    assert role == "converse"
    text = str(payload)
    for expected in ("add a banner", "simple_change", "must_decide", "calc.py"):
        assert expected in text
