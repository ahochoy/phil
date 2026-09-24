import shlex
import sys

from phil.agents.fake import ScriptedAgentFactory, Turn
from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.agents.tools import CommandLog
from phil.contracts import ImplementInput, SelfCheck, Task, TaskResult
from phil.packets import build_packet

PY = shlex.quote(sys.executable)


def result() -> TaskResult:
    check = SelfCheck(assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[])
    return TaskResult(phase="red", summary="s", files_changed=[], tests_added=[], self_check=check)


def packet(feedback=()):
    task = Task(id="CALC-001", description="d", acceptance_criteria=["c"])
    contract = ImplementInput(task=task, phase="red", test_cmd="pytest", feedback=list(feedback))
    return build_packet("implementer", contract, budget_tokens=4000)


def test_denied_commands_are_collected(config, conn, tmp_path):
    outputs: list[str] = []

    def script(turn: Turn) -> TaskResult:
        outputs.append(turn.tools["run_shell"]("make build"))
        return result()

    log = CommandLog()
    ctx = AgentContext(
        config=config, conn=conn, layer="run", workdir=tmp_path,
        factory=ScriptedAgentFactory({"implementer": [script]}), command_log=log,
    )
    invoke_agent(get_spec("implementer"), packet(), ctx, node="implement", task_id="CALC-001")
    assert outputs[0].startswith("DENIED:")
    assert log.denied == ["make build"]
    assert log.commands == []


def test_extra_allow_permits_an_approved_command(config, conn, tmp_path):
    (tmp_path / "build.py").write_text("print('built')\n")
    command = f"{PY} build.py"
    outputs: list[str] = []

    def script(turn: Turn) -> TaskResult:
        outputs.append(turn.tools["run_shell"](command))
        return result()

    log = CommandLog()
    original_allow = list(config.shell.allow)
    ctx = AgentContext(
        config=config, conn=conn, layer="run", workdir=tmp_path,
        factory=ScriptedAgentFactory({"implementer": [script]}), command_log=log, extra_allow=(command,),
    )
    invoke_agent(get_spec("implementer"), packet(), ctx, node="implement", task_id="CALC-001")
    assert outputs[0].startswith("exit_code: 0")
    assert "built" in outputs[0]
    assert log.commands == [command]
    assert log.denied == []
    assert config.shell.allow == original_allow


def test_feedback_reaches_the_packet():
    assert "Human hint: use subtraction" in packet(["Human hint: use subtraction"]).render()
