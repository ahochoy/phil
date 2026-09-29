from phil.agents.fake import ScriptedAgentFactory
from phil.chat.btw import ask_btw
from phil.contracts import Brief, RunStatus
from tests.chat.conftest import goal, plan


def test_btw_passes_context_and_uses_the_snapshot(chat_ctx, tmp_path):
    seen = {}

    def answer(turn):
        seen["workdir"] = turn.workdir
        seen["payload"] = str(turn.payload)
        return Brief(headline="CALC-002 is in the green phase", points=["tests ran twice"])

    factory = ScriptedAgentFactory({"btw": [answer]})
    run = RunStatus(run_id="r-1", state="running", tasks_done=1, tasks_total=2, current_node="implement")
    brief = ask_btw(
        chat_ctx(factory), "why is it slow?", goal=goal(), plan=plan(), run=run,
        recent_events=["node implement"], pending_question=None, tree=tmp_path,
    )
    assert brief.headline == "CALC-002 is in the green phase"
    assert seen["workdir"] == tmp_path
    assert "why is it slow?" in seen["payload"] and "r-1" in seen["payload"]


def test_btw_without_a_snapshot_has_no_workdir(chat_ctx):
    factory = ScriptedAgentFactory({"btw": [lambda turn: Brief(headline=str(turn.workdir))]})
    assert ask_btw(chat_ctx(factory), "hi").headline == "None"
